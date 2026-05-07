"""
exam_engine.sources.source_normalizer — unify divergent source layouts.

Different sites (مادتي / كتبي / منهجي) ship sample sheets in
incompatible shapes. This module reduces them to a single canonical
``NormalizedSample``: a list of question dicts with consistent keys.

Sources can supply their own raw structure two ways:

    1. ``raw_content`` is a JSON string with a known shape:
       ``{"questions": [...], "meta": {...}}`` — providers just dump
       their parsed dict and we accept it directly.

    2. ``raw_content`` is plain Arabic text. We run a deterministic
       block extractor that handles common Saudi exam formats
       (numbered questions, "اختر الإجابة الصحيحة", "ضع علامة صح").

Pure module. No DB / GPT / network.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from app.exam_engine.schemas import (
    QTYPE_FILL_BLANK,
    QTYPE_MCQ,
    QTYPE_SHORT,
    QTYPE_TRUE_FALSE,
    QTYPES_ALL,
)
from app.exam_engine.sources.base import SourceSample


# ──────────────────────────────────────────────────────────────────────
# DTOs
# ──────────────────────────────────────────────────────────────────────


@dataclass
class CandidateQuestion:
    """A normalised question dict before it becomes an ``ExamQuestion``."""

    text: str
    type: str = QTYPE_SHORT
    choices: tuple[str, ...] = ()
    correct_answer: Any = ""
    marks: float = 1.0
    learning_outcome: str | None = None
    difficulty: str = "medium"
    source_index: int = 0  # original numbering inside the source

    def is_well_formed(self) -> bool:
        if not self.text or len(self.text.strip()) < 3:
            return False
        if self.type not in QTYPES_ALL:
            return False
        if self.type == QTYPE_MCQ and not self.choices:
            return False
        return True


@dataclass
class NormalizedSample:
    """The output of ``normalize_exam_source``."""

    provider: str
    title: str
    questions: tuple[CandidateQuestion, ...]
    meta: dict = field(default_factory=dict)

    @property
    def question_count(self) -> int:
        return len(self.questions)


# ──────────────────────────────────────────────────────────────────────
# Public entry-point
# ──────────────────────────────────────────────────────────────────────


def normalize_exam_source(sample: SourceSample) -> NormalizedSample:
    """Turn ``sample`` into a ``NormalizedSample``.

    The function never raises — malformed input yields an empty
    ``NormalizedSample`` so the calling pipeline can move on.
    """
    if not sample or not (sample.raw_content or "").strip():
        return NormalizedSample(
            provider=sample.provider if sample else "",
            title=sample.title if sample else "",
            questions=(),
        )

    raw = sample.raw_content.strip()

    # ── 1. JSON-encoded payload (preferred — providers control shape)
    if raw.startswith("{") or raw.startswith("["):
        try:
            decoded = json.loads(raw)
            return _from_json(sample, decoded)
        except (ValueError, TypeError):
            # Fall through to the heuristic parser.
            pass

    # ── 2. Plain Arabic text — run the heuristic extractor
    return _from_text(sample, raw)


# ──────────────────────────────────────────────────────────────────────
# JSON path
# ──────────────────────────────────────────────────────────────────────


def _from_json(sample: SourceSample, decoded: Any) -> NormalizedSample:
    if isinstance(decoded, list):
        decoded = {"questions": decoded}
    if not isinstance(decoded, dict):
        return NormalizedSample(provider=sample.provider, title=sample.title, questions=())

    questions_raw = decoded.get("questions") or []
    candidates: list[CandidateQuestion] = []
    for idx, raw_q in enumerate(questions_raw, start=1):
        if not isinstance(raw_q, dict):
            continue
        text = str(raw_q.get("text") or raw_q.get("question") or "").strip()
        if not text:
            continue
        qtype = str(raw_q.get("type") or _infer_type(text, raw_q.get("choices"))).strip()
        if qtype not in QTYPES_ALL:
            qtype = QTYPE_SHORT

        raw_choices = raw_q.get("choices") or raw_q.get("options") or ()
        choices = tuple(str(c).strip() for c in raw_choices if str(c).strip())

        correct = raw_q.get("correct_answer") or raw_q.get("answer") or ""
        marks = _coerce_float(raw_q.get("marks"), default=1.0)

        candidates.append(CandidateQuestion(
            text=text,
            type=qtype,
            choices=choices,
            correct_answer=correct,
            marks=marks,
            learning_outcome=_safe_str(raw_q.get("learning_outcome")),
            difficulty=str(raw_q.get("difficulty") or "medium"),
            source_index=idx,
        ))

    meta = decoded.get("meta") or {}
    if not isinstance(meta, dict):
        meta = {}
    return NormalizedSample(
        provider=sample.provider,
        title=str(decoded.get("title") or sample.title),
        questions=tuple(candidates),
        meta=meta,
    )


# ──────────────────────────────────────────────────────────────────────
# Heuristic text path
# ──────────────────────────────────────────────────────────────────────

# Matches "1)", "1-", "1.", "السؤال الأول:", "س1:".
_QUESTION_HEAD_RE = re.compile(
    r"(?:^|\n)\s*"
    r"(?:"
    r"(?P<num>\d{1,3})\s*[\.\)\-\:]"
    r"|س\s*(?P<num2>\d{1,3})\s*[\.\)\-\:]"
    r"|السؤال\s+(?P<ord>الأول|الثاني|الثالث|الرابع|الخامس|السادس|السابع|الثامن|التاسع|العاشر)\s*[\.\)\-\:]"
    r")"
    r"\s*(?P<body>.+?)"
    r"(?=(?:\n\s*(?:\d{1,3}\s*[\.\)\-\:]|س\s*\d{1,3}|السؤال\s+(?:الأول|الثاني|الثالث|الرابع|الخامس|السادس|السابع|الثامن|التاسع|العاشر)))|\Z)",
    re.MULTILINE | re.DOTALL,
)

# MCQ choice prefixes: "أ)", "ب)", "ج)", "د)", "1)", "A)" ...
_CHOICE_RE = re.compile(
    r"(?:^|\s)(?:أ|ب|ج|د|هـ|١|٢|٣|٤|١\.|٢\.|[a-dA-D]|\d)[\)\.\-]\s*(?P<text>[^\n]+)",
    re.MULTILINE,
)

# Fill-blank marker: ".......", "(......)", "________"
_BLANK_RE = re.compile(r"\.{3,}|_{3,}|…+")

_TRUE_FALSE_TRIGGERS = ("صح أو خطأ", "ضع علامة", "صح أم خطأ")
_MCQ_TRIGGERS = ("اختر", "اختر الإجابة", "اختر الإجابة الصحيحة")


def _from_text(sample: SourceSample, text: str) -> NormalizedSample:
    candidates: list[CandidateQuestion] = []
    text = _normalize_unicode_whitespace(text)
    matches = list(_QUESTION_HEAD_RE.finditer(text))
    for idx, m in enumerate(matches, start=1):
        body = (m.group("body") or "").strip()
        if not body:
            continue
        # Split body into the question stem and any choices.
        stem, choices = _split_stem_and_choices(body)
        qtype = _infer_type(stem, choices, surrounding=text)
        candidates.append(CandidateQuestion(
            text=stem.strip(),
            type=qtype,
            choices=choices,
            correct_answer="",  # external sources rarely include answers
            marks=1.0,
            source_index=idx,
        ))

    title = _extract_title(text) or sample.title
    return NormalizedSample(
        provider=sample.provider,
        title=title,
        questions=tuple(candidates),
        meta={"raw_length": len(text)},
    )


def _split_stem_and_choices(body: str) -> tuple[str, tuple[str, ...]]:
    """Pull MCQ choices out of the body."""
    choices: list[str] = []
    for cm in _CHOICE_RE.finditer(body):
        choice = (cm.group("text") or "").strip()
        if choice and len(choice) >= 1:
            choices.append(choice)
    if not choices:
        return body.strip(), ()
    # Stem = body up to the first choice marker.
    first = _CHOICE_RE.search(body)
    if first:
        stem = body[: first.start()].strip().rstrip(":،")
    else:
        stem = body.strip()
    return stem, tuple(choices[:6])


def _infer_type(
    text: str,
    choices: tuple[str, ...] | list[str] | None = None,
    *,
    surrounding: str = "",
) -> str:
    norm = (text or "").lower()
    surrounding = (surrounding or "").lower()

    if choices:
        return QTYPE_MCQ

    if any(t in surrounding for t in _TRUE_FALSE_TRIGGERS) or any(
        t in norm for t in _TRUE_FALSE_TRIGGERS
    ):
        return QTYPE_TRUE_FALSE

    if _BLANK_RE.search(text or ""):
        return QTYPE_FILL_BLANK

    if any(t in surrounding for t in _MCQ_TRIGGERS) or any(
        t in norm for t in _MCQ_TRIGGERS
    ):
        return QTYPE_MCQ

    return QTYPE_SHORT


def _extract_title(text: str) -> str | None:
    """First non-empty line shorter than 80 chars makes a decent title."""
    for line in text.splitlines():
        line = line.strip()
        if 4 <= len(line) <= 80:
            return line
    return None


def _normalize_unicode_whitespace(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    # Replace odd zero-width / formatting controls.
    for ch in "\u200B\u200C\u200D\u202A\u202B\u202C\u202D\u202E\uFEFF":
        s = s.replace(ch, "")
    return s


def _coerce_float(v: Any, *, default: float) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _safe_str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


__all__ = [
    "CandidateQuestion",
    "NormalizedSample",
    "normalize_exam_source",
]
