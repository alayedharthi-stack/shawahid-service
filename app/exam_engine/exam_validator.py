"""
exam_engine.exam_validator — sanity checks for a generated exam.

Pure module. No DB / GPT / network.

The generator is allowed to ship "best effort" output, but the
validator is the gate before anything reaches the renderer or the
teacher. Errors block; warnings surface as advisories.
"""
from __future__ import annotations

from app.exam_engine.schemas import (
    QTYPE_FILL_BLANK,
    QTYPE_MATCH,
    QTYPE_MCQ,
    QTYPE_SHORT,
    QTYPE_TRUE_FALSE,
    ExamQuestion,
    GeneratedExam,
    ValidationIssue,
    ValidationResult,
)
from app.services.intents import normalize


# Stage-fit hint: any of these substrings inside a question text mark
# it as "above primary". The check is conservative — we only flag
# strong mismatches (e.g. calculus terms in a primary maths exam).
_STAGE_FIT: dict[str, tuple[str, ...]] = {
    "primary": ("تفاضل", "تكامل", "نهايات", "جذور تربيعية معقدة"),
}


def validate_exam(exam: GeneratedExam) -> ValidationResult:
    """Run every check and return a ``ValidationResult``.

    The validator never raises — every problem becomes a
    ``ValidationIssue`` so the caller can present them all at once.
    """
    issues: list[ValidationIssue] = []

    if not exam.questions:
        issues.append(ValidationIssue(
            code="no_questions",
            message="الاختبار لا يحتوي على أسئلة.",
        ))
        return ValidationResult(tuple(issues))

    issues.extend(_check_each_question(exam.questions))
    issues.extend(_check_marks_total(exam))
    issues.extend(_check_duplicates(exam.questions))
    issues.extend(_check_stage_fit(exam))
    issues.extend(_check_off_topic(exam))

    return ValidationResult(tuple(issues))


# ──────────────────────────────────────────────────────────────────────
# Per-question checks
# ──────────────────────────────────────────────────────────────────────


def _check_each_question(
    questions: tuple[ExamQuestion, ...],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for q in questions:
        if not q.text or not q.text.strip():
            issues.append(ValidationIssue(
                code="empty_text",
                message="نص السؤال فارغ.",
                question_id=q.id,
            ))

        # ── Correct answer presence ──────────────────────────────────
        if q.type == QTYPE_MCQ:
            if not q.choices:
                issues.append(ValidationIssue(
                    code="mcq_no_choices",
                    message="سؤال اختيار من متعدد بدون خيارات.",
                    question_id=q.id,
                ))
            elif not isinstance(q.correct_answer, int):
                issues.append(ValidationIssue(
                    code="mcq_no_answer",
                    message="سؤال اختيار من متعدد بدون إجابة صحيحة.",
                    question_id=q.id,
                ))
            elif not (0 <= q.correct_answer < len(q.choices)):
                issues.append(ValidationIssue(
                    code="mcq_answer_out_of_range",
                    message="رقم الإجابة الصحيحة خارج عدد الخيارات.",
                    question_id=q.id,
                ))

        elif q.type == QTYPE_TRUE_FALSE:
            ans = str(q.correct_answer or "").strip()
            if ans not in ("صح", "خطأ", "true", "false", "True", "False"):
                issues.append(ValidationIssue(
                    code="tf_bad_answer",
                    message="إجابة سؤال صح/خطأ يجب أن تكون «صح» أو «خطأ».",
                    question_id=q.id,
                ))

        elif q.type in (QTYPE_FILL_BLANK, QTYPE_SHORT):
            if not str(q.correct_answer or "").strip():
                issues.append(ValidationIssue(
                    code="missing_answer",
                    message="السؤال بدون إجابة صحيحة.",
                    question_id=q.id,
                ))

        elif q.type == QTYPE_MATCH:
            if not q.choices:
                issues.append(ValidationIssue(
                    code="match_no_pairs",
                    message="سؤال مطابقة بدون عناصر.",
                    question_id=q.id,
                ))

        else:
            issues.append(ValidationIssue(
                code="unknown_qtype",
                message=f"نوع سؤال غير مدعوم: {q.type}",
                question_id=q.id,
            ))

        # ── Marks ────────────────────────────────────────────────────
        if q.marks <= 0:
            issues.append(ValidationIssue(
                code="non_positive_marks",
                message="درجات السؤال يجب أن تكون أكبر من صفر.",
                question_id=q.id,
            ))

    return issues


# ──────────────────────────────────────────────────────────────────────
# Aggregate checks
# ──────────────────────────────────────────────────────────────────────


def _check_marks_total(exam: GeneratedExam) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    actual = exam.actual_total_marks
    expected = exam.profile.total_marks
    # Allow 0.01 epsilon for float rounding.
    if abs(actual - expected) > 0.01:
        issues.append(ValidationIssue(
            code="marks_mismatch",
            message=(
                f"مجموع درجات الأسئلة ({actual}) لا يساوي الدرجة الكلية "
                f"({expected})."
            ),
        ))
    return issues


def _check_duplicates(
    questions: tuple[ExamQuestion, ...],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    seen: dict[str, str] = {}
    for q in questions:
        key = normalize(q.text)
        if key in seen:
            issues.append(ValidationIssue(
                code="duplicate_question",
                message=f"سؤال مكرر مع: {seen[key]}",
                question_id=q.id,
            ))
        else:
            seen[key] = q.id
    return issues


def _check_stage_fit(exam: GeneratedExam) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    stage = (exam.profile.stage or "").strip()
    # Map Arabic stage labels to the canonical key used in _STAGE_FIT.
    stage_key = "primary" if "ابتدائي" in stage else stage
    forbidden = _STAGE_FIT.get(stage_key, ())
    if not forbidden:
        return issues
    for q in exam.questions:
        norm = normalize(q.text)
        for term in forbidden:
            if term in norm:
                issues.append(ValidationIssue(
                    code="stage_mismatch",
                    message=f"محتوى ربما يفوق المرحلة: «{term}»",
                    question_id=q.id,
                    severity="warning",
                ))
                break
    return issues


def _check_off_topic(exam: GeneratedExam) -> list[ValidationIssue]:
    """If a topic / lesson is set, warn when a question text contains
    none of its tokens. We never *fail* on this — topic detection is
    fuzzy by nature."""
    issues: list[ValidationIssue] = []
    topic = (exam.request.topic or exam.request.lesson or exam.request.unit or "").strip()
    if not topic:
        return issues
    norm_topic_tokens = [t for t in normalize(topic).split() if len(t) >= 3]
    if not norm_topic_tokens:
        return issues
    for q in exam.questions:
        norm_q = normalize(q.text)
        if not any(token in norm_q for token in norm_topic_tokens):
            issues.append(ValidationIssue(
                code="possibly_off_topic",
                message="السؤال قد لا يرتبط بالموضوع المحدد.",
                question_id=q.id,
                severity="warning",
            ))
    return issues


__all__ = ["validate_exam"]
