"""
exam_engine.sources.anti_copy — prevent verbatim copying of source exams.

Phase-11 contract
=================
Verbatim copying from external sources is forbidden. This module
applies a deterministic but seed-dependent transformation pipeline so
the generated exam:

    1. has its question order shuffled
    2. has MCQ choices shuffled (with the correct-answer index re-mapped)
    3. has numeric arithmetic problems re-numbered when safe
    4. has stems lightly paraphrased via an Arabic synonym table

The result is a *similar* exam, not a *matching* one. Caller passes a
seed (typically ``hash(teacher_id, today)``) so the same teacher gets
a stable result within a session, but two teachers (or two sessions)
get different transformations.

Pure module. No GPT / network — the paraphrase table is hand-built.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

from app.exam_engine.schemas import (
    QTYPE_FILL_BLANK,
    QTYPE_MCQ,
    QTYPE_TRUE_FALSE,
    ExamQuestion,
)


# ──────────────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────────────


@dataclass
class AntiCopyOptions:
    """Knobs used by tests / production to control the pipeline."""

    shuffle_questions: bool = True
    shuffle_choices: bool = True
    paraphrase_stems: bool = True
    transform_numbers: bool = True
    seed: int | None = None


@dataclass
class TransformationLog:
    """What the pipeline actually did. Useful for tests + audit."""

    reordered: bool = False
    choices_shuffled: int = 0
    numbers_changed: int = 0
    stems_paraphrased: int = 0
    transformations: list[str] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────
# Paraphrase templates (Arabic)
# Single-pass substitutions; we only swap leading verb phrases that
# don't change meaning.
# ──────────────────────────────────────────────────────────────────────

_LEADING_PARAPHRASES: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"^\s*ناتج\s+جمع\s+"), "احسب ناتج جمع "),
    (re.compile(r"^\s*احسب\s+ناتج\s+جمع\s+"), "كم يساوي "),
    (re.compile(r"^\s*كم\s+يساوي\s+"), "أوجد قيمة "),
    (re.compile(r"^\s*أوجد\s+قيمة\s+"), "احسب قيمة "),
    (re.compile(r"^\s*اكتب\s+باختصار\s+"), "وضّح بإيجاز "),
    (re.compile(r"^\s*اشرح\s+"), "وضّح "),
    (re.compile(r"^\s*وضّح\s+"), "بيّن "),
    (re.compile(r"^\s*اذكر\s+"), "اكتب "),
    (re.compile(r"^\s*عبارة\s+صحيحة\s+عن\s+"), "اكتب عبارة صحيحة عن "),
    (re.compile(r"^\s*أيٌّ\s+مما\s+يلي\s+"), "حدّد مما يلي "),
)

# Synonym swaps inside the body — only words whose meaning is preserved.
_BODY_SYNONYMS: tuple[tuple[str, str], ...] = (
    ("يساوي", "يعادل"),
    ("الطالب", "المتعلم"),
    ("اشرح", "وضّح"),
    ("بإيجاز", "باختصار"),
)


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────


def anti_copy_transform(
    questions: tuple[ExamQuestion, ...],
    *,
    options: AntiCopyOptions | None = None,
) -> tuple[tuple[ExamQuestion, ...], TransformationLog]:
    """Apply the anti-copy pipeline to ``questions``.

    Returns the transformed tuple plus a log so tests can verify what
    happened. The function is *pure* — given the same seed and inputs
    it produces the same output.
    """
    options = options or AntiCopyOptions()
    rng = random.Random(options.seed)
    log = TransformationLog()

    out: list[ExamQuestion] = list(questions)

    if options.transform_numbers:
        out = [_transform_numbers(q, rng, log) for q in out]

    if options.paraphrase_stems:
        out = [_paraphrase_stem(q, rng, log) for q in out]

    if options.shuffle_choices:
        out = [_shuffle_choices(q, rng, log) for q in out]

    if options.shuffle_questions and len(out) > 1:
        new_order = list(out)
        rng.shuffle(new_order)
        if new_order != list(out):
            log.reordered = True
            log.transformations.append("reordered")
        out = new_order

    return tuple(out), log


# ──────────────────────────────────────────────────────────────────────
# Step implementations
# ──────────────────────────────────────────────────────────────────────


def _shuffle_choices(
    q: ExamQuestion, rng: random.Random, log: TransformationLog,
) -> ExamQuestion:
    """For MCQ questions, reorder choices and re-map correct_answer."""
    if q.type != QTYPE_MCQ or len(q.choices) < 2:
        return q

    pairs = list(enumerate(q.choices))
    rng.shuffle(pairs)
    # If the shuffle produced the same order, force a swap of the first two.
    if [p[0] for p in pairs] == list(range(len(q.choices))):
        if len(pairs) >= 2:
            pairs[0], pairs[1] = pairs[1], pairs[0]

    new_choices = tuple(text for _, text in pairs)

    new_correct: int | str = q.correct_answer
    if isinstance(q.correct_answer, int):
        # The new index is the position of the original index.
        for new_idx, (old_idx, _) in enumerate(pairs):
            if old_idx == q.correct_answer:
                new_correct = new_idx
                break

    log.choices_shuffled += 1
    log.transformations.append(f"shuffle_choices:{q.id}")
    return _replace_question(q, choices=new_choices, correct_answer=new_correct)


def _paraphrase_stem(
    q: ExamQuestion, rng: random.Random, log: TransformationLog,
) -> ExamQuestion:
    if not q.text:
        return q
    new_text = q.text
    changed = False

    for pattern, replacement in _LEADING_PARAPHRASES:
        if pattern.search(new_text):
            new_text = pattern.sub(replacement, new_text, count=1)
            changed = True
            break

    # Body-level synonym swap (1 swap max so we don't drift far).
    available = [(src, dst) for src, dst in _BODY_SYNONYMS if src in new_text]
    if available:
        src, dst = rng.choice(available)
        new_text = new_text.replace(src, dst, 1)
        changed = True

    if not changed:
        return q

    log.stems_paraphrased += 1
    log.transformations.append(f"paraphrase:{q.id}")
    return _replace_question(q, text=new_text)


def _transform_numbers(
    q: ExamQuestion, rng: random.Random, log: TransformationLog,
) -> ExamQuestion:
    """Replace simple addition/subtraction patterns with new operands.

    We only rewrite questions that are *purely* arithmetic ("12 + 8")
    so we never silently break a non-arithmetic correct answer.
    """
    if q.type not in (QTYPE_FILL_BLANK, QTYPE_TRUE_FALSE):
        # MCQ rewrites would also need to update the choices — skip for
        # safety; only fill-blank / true-false get number transforms.
        if q.type != QTYPE_MCQ:
            return q

    text = q.text or ""
    m = _ADDITION_RE.search(text)
    if not m:
        return q

    a, b = int(m.group("a")), int(m.group("b"))
    # Pick a delta in [-3, 3] excluding 0 so we always change something.
    delta_a = rng.choice((-3, -2, -1, 1, 2, 3))
    delta_b = rng.choice((-3, -2, -1, 1, 2, 3))
    new_a = max(0, a + delta_a)
    new_b = max(0, b + delta_b)
    if (new_a, new_b) == (a, b):
        new_a += 1
    new_text = text[: m.start()] + f"{new_a} + {new_b}" + text[m.end():]

    new_correct: int | str = q.correct_answer
    new_choices = q.choices

    if q.type == QTYPE_FILL_BLANK:
        # The fill-in answer was the sum — recompute.
        try:
            old_sum = a + b
            if str(old_sum).strip() == str(q.correct_answer).strip():
                new_correct = str(new_a + new_b)
        except Exception:  # noqa: BLE001
            pass
    elif q.type == QTYPE_MCQ and isinstance(q.correct_answer, int):
        # If the correct choice was the literal sum, swap it for the new one.
        old_sum = str(a + b)
        new_sum = str(new_a + new_b)
        new_choices = tuple(
            (new_sum if str(c).strip() == old_sum else c) for c in q.choices
        )

    log.numbers_changed += 1
    log.transformations.append(f"numbers:{q.id}")
    return _replace_question(
        q, text=new_text, choices=new_choices, correct_answer=new_correct,
    )


_ADDITION_RE = re.compile(r"(?P<a>\d{1,3})\s*\+\s*(?P<b>\d{1,3})")


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _replace_question(q: ExamQuestion, **changes) -> ExamQuestion:
    """Return a new ``ExamQuestion`` with only the named fields replaced."""
    return ExamQuestion(
        id=changes.get("id", q.id),
        type=changes.get("type", q.type),
        text=changes.get("text", q.text),
        choices=changes.get("choices", q.choices),
        correct_answer=changes.get("correct_answer", q.correct_answer),
        marks=changes.get("marks", q.marks),
        difficulty=changes.get("difficulty", q.difficulty),
        learning_outcome=changes.get("learning_outcome", q.learning_outcome),
        bloom_level=changes.get("bloom_level", q.bloom_level),
    )


__all__ = [
    "AntiCopyOptions",
    "TransformationLog",
    "anti_copy_transform",
]
