"""
exam_engine.exam_generator — assemble exam questions for a request.

Phase-10 contract
=================
The generator never invents content. It assembles exams from one of:

    1. ``LearningOutcome`` lists (e.g. parsed from a teacher's plan)
    2. The bundled local sample bank
    3. A teacher-supplied topic + a hand-rolled fill-blank fallback

If none of those sources is usable, the generator returns
``GenerationFailure(reason=...)`` so the webhook can ask for the
missing inputs instead of silently fabricating content.

Pure module. No DB / GPT / network / Playwright.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.curriculum_engine.schemas import LearningOutcome
from app.exam_engine.exam_profile import build_exam_profile
from app.exam_engine.schemas import (
    EXAM_TYPE_LABELS_AR,
    QTYPE_MCQ,
    QTYPE_SHORT,
    QTYPE_TRUE_FALSE,
    SOURCE_CURRICULUM,
    SOURCE_MANUAL_TOPIC,
    SOURCE_SAMPLE_BANK,
    SOURCE_TEACHER_FILE,
    ExamProfile,
    ExamQuestion,
    ExamRequest,
    GeneratedExam,
)
from app.exam_engine.sources.local_samples import lookup_sample_questions
from app.services.intents import normalize


# ──────────────────────────────────────────────────────────────────────
# Failure DTO
# ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GenerationFailure:
    """Returned when the generator cannot honor a request safely."""

    code: str
    reason: str
    missing: tuple[str, ...] = ()


# ──────────────────────────────────────────────────────────────────────
# Public entry-point
# ──────────────────────────────────────────────────────────────────────


def generate_exam(
    request: ExamRequest,
    *,
    profile: ExamProfile | None = None,
    learning_outcomes: tuple[LearningOutcome, ...] = (),
    teacher_topic: str | None = None,
) -> GeneratedExam | GenerationFailure:
    """Build an exam for ``request``.

    The caller may pre-build an ``ExamProfile`` (recommended); if not,
    we synthesise a minimal one from the request alone.

    The function never raises. It returns either:
        • ``GeneratedExam`` on success, OR
        • ``GenerationFailure`` describing what's missing.
    """
    missing = request.required_fields_missing()
    if missing:
        return GenerationFailure(
            code="missing_required_fields",
            reason="بيانات أساسية ناقصة لإنشاء الاختبار.",
            missing=missing,
        )

    profile = profile or build_exam_profile(request=request)

    questions = _gather_questions(
        request=request,
        learning_outcomes=learning_outcomes,
        teacher_topic=teacher_topic,
    )
    if not questions:
        return GenerationFailure(
            code="no_source_content",
            reason=(
                "أحتاج تحديد الدرس أو رفع نموذج مناسب."
                " يمكنك إرسال خطة الدرس أو نموذج اختبار سابق."
            ),
            missing=("topic_or_sample",),
        )

    questions, dedup_warning = _deduplicate(questions)
    questions, trimmed = _cap_to_requested(questions, request.total_questions)
    questions = _allocate_marks(questions, target_total=request.total_marks)

    warnings: list[str] = []
    if dedup_warning:
        warnings.append("تمت إزالة أسئلة مكررة.")
    if trimmed:
        warnings.append("تم اقتطاع الأسئلة الزائدة عن العدد المطلوب.")
    if len(questions) < request.total_questions:
        warnings.append(
            f"عدد الأسئلة المتوفرة ({len(questions)}) أقل من المطلوب "
            f"({request.total_questions})."
        )

    notes: list[str] = []
    if learning_outcomes:
        notes.append(f"مرتبط بـ {len(learning_outcomes)} نواتج تعلم.")
    notes.append(f"مصدر الأسئلة: {request.source_mode}")

    return GeneratedExam(
        profile=profile,
        questions=tuple(questions),
        request=request,
        warnings=tuple(warnings),
        notes=tuple(notes),
    )


# ──────────────────────────────────────────────────────────────────────
# Source selection
# ──────────────────────────────────────────────────────────────────────


def _gather_questions(
    *,
    request: ExamRequest,
    learning_outcomes: tuple[LearningOutcome, ...],
    teacher_topic: str | None,
) -> list[ExamQuestion]:
    """Collect candidate questions from the requested source(s)."""
    if request.source_mode == SOURCE_CURRICULUM and learning_outcomes:
        return _from_outcomes(learning_outcomes, request)

    if request.source_mode == SOURCE_SAMPLE_BANK:
        return list(lookup_sample_questions(
            subject=request.subject, stage=request.stage,
        ))

    if request.source_mode == SOURCE_TEACHER_FILE and learning_outcomes:
        # We treat outcomes extracted from the teacher's file as the
        # primary source. Sample bank acts as gentle fallback.
        out = _from_outcomes(learning_outcomes, request)
        if out:
            return out
        return list(lookup_sample_questions(
            subject=request.subject, stage=request.stage,
        ))

    if request.source_mode == SOURCE_MANUAL_TOPIC:
        topic = (teacher_topic or request.topic or request.lesson or request.unit or "").strip()
        if not topic:
            return []
        return _from_topic(topic, request)

    # Unknown / unsupported source mode → nothing.
    return []


# ──────────────────────────────────────────────────────────────────────
# Source-specific assemblers
# ──────────────────────────────────────────────────────────────────────


def _from_outcomes(
    outcomes: tuple[LearningOutcome, ...],
    request: ExamRequest,
) -> list[ExamQuestion]:
    """Map each learning outcome to one short question.

    The wording is deliberately simple: "اشرح / اكتب / وضّح + raw outcome".
    Real curriculum-based question synthesis lands in a future phase.
    """
    out: list[ExamQuestion] = []
    for idx, outcome in enumerate(outcomes, start=1):
        text = _outcome_to_question(outcome.raw)
        out.append(ExamQuestion(
            id=ExamQuestion.new_id(),
            type=QTYPE_SHORT,
            text=text,
            correct_answer="—",  # subjective, marked manually
            marks=2.0,
            difficulty=request.difficulty,
            learning_outcome=outcome.raw,
            bloom_level=outcome.bloom_level,
        ))
    return out


def _outcome_to_question(raw_outcome: str) -> str:
    raw = raw_outcome.strip()
    if not raw:
        return "اشرح..."
    # If outcome already starts with a learner verb ("يحل", "يفسر")
    # turn it into a directive: "اشرح كيف يحل الطالب ...".
    return f"اشرح {raw}"


def _from_topic(topic: str, request: ExamRequest) -> list[ExamQuestion]:
    """Hand-rolled, deterministic question scaffold for a manual topic.

    We generate 3 scaffolded questions (one per supported type the
    request asked for). These are *placeholders* — they always need
    the teacher's review before printing.
    """
    qtypes = request.question_types or (QTYPE_MCQ, QTYPE_TRUE_FALSE, QTYPE_SHORT)
    out: list[ExamQuestion] = []
    if QTYPE_MCQ in qtypes:
        out.append(ExamQuestion(
            id=ExamQuestion.new_id(),
            type=QTYPE_MCQ,
            text=f"أيٌّ مما يلي يتعلق بـ «{topic}»؟",
            choices=("الخيار الأول", "الخيار الثاني", "الخيار الثالث", "الخيار الرابع"),
            correct_answer=0,
            marks=1,
            difficulty=request.difficulty,
            learning_outcome=topic,
        ))
    if QTYPE_TRUE_FALSE in qtypes:
        out.append(ExamQuestion(
            id=ExamQuestion.new_id(),
            type=QTYPE_TRUE_FALSE,
            text=f"عبارة صحيحة عن «{topic}»: ............",
            correct_answer="صح",
            marks=1,
            difficulty=request.difficulty,
            learning_outcome=topic,
        ))
    if QTYPE_SHORT in qtypes:
        out.append(ExamQuestion(
            id=ExamQuestion.new_id(),
            type=QTYPE_SHORT,
            text=f"اكتب باختصار ما تعرفه عن «{topic}».",
            correct_answer="—",
            marks=2,
            difficulty=request.difficulty,
            learning_outcome=topic,
        ))
    return out


# ──────────────────────────────────────────────────────────────────────
# Post-processing
# ──────────────────────────────────────────────────────────────────────


def _deduplicate(
    questions: list[ExamQuestion],
) -> tuple[list[ExamQuestion], bool]:
    seen: set[str] = set()
    out: list[ExamQuestion] = []
    removed = False
    for q in questions:
        key = normalize(q.text)
        if not key or key in seen:
            removed = removed or bool(key)
            continue
        seen.add(key)
        out.append(q)
    return out, removed


def _cap_to_requested(
    questions: list[ExamQuestion],
    target: int,
) -> tuple[list[ExamQuestion], bool]:
    if target and len(questions) > target:
        return questions[:target], True
    return questions, False


def _allocate_marks(
    questions: list[ExamQuestion],
    *,
    target_total: int,
) -> list[ExamQuestion]:
    """Scale per-question marks so the sum equals ``target_total``.

    Preserves relative weighting between question types. Distributes
    rounding remainder to the first questions so the total matches
    exactly (the validator is strict about this).
    """
    if not questions or target_total <= 0:
        return questions

    current_sum = sum(q.marks for q in questions)
    if current_sum <= 0:
        # Even split as a safe baseline.
        per = round(target_total / len(questions), 2)
        out: list[ExamQuestion] = []
        running = 0.0
        for idx, q in enumerate(questions):
            mark = per
            if idx == len(questions) - 1:
                mark = round(target_total - running, 2)
            running += mark
            out.append(_with_marks(q, mark))
        return out

    scale = target_total / current_sum
    out = []
    running = 0.0
    for idx, q in enumerate(questions):
        if idx == len(questions) - 1:
            mark = round(target_total - running, 2)
        else:
            mark = round(q.marks * scale, 2)
            running += mark
        out.append(_with_marks(q, mark))
    return out


def _with_marks(q: ExamQuestion, marks: float) -> ExamQuestion:
    return ExamQuestion(
        id=q.id,
        type=q.type,
        text=q.text,
        choices=q.choices,
        correct_answer=q.correct_answer,
        marks=marks,
        difficulty=q.difficulty,
        learning_outcome=q.learning_outcome,
        bloom_level=q.bloom_level,
    )


__all__ = [
    "GenerationFailure",
    "generate_exam",
    "EXAM_TYPE_LABELS_AR",
]
