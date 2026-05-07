"""
exam_engine.sources.curriculum_filter — gate samples by curriculum context.

Phase-11 contract
=================
A sample that's high-quality but for the wrong semester is *worse*
than no sample at all (the teacher would have to throw it away). This
module checks each sample's metadata against the request's
curriculum context using the existing ``curriculum_engine``:

    • detect_semester  — block when the source semester ≠ request
    • detect_week      — flag when weeks are very far apart
    • detect_document_intent — drop samples whose intent isn't an
                              assessment (e.g. lesson plans miscategorised)

Pure module. Imports only from ``curriculum_engine`` (no DB / GPT).
"""
from __future__ import annotations

from dataclasses import dataclass

from app.curriculum_engine.document_intent import detect_document_intent
from app.curriculum_engine.saudi_curriculum import ASSESSMENT_HEADINGS
from app.curriculum_engine.schemas import (
    DOC_INTENT_ASSESSMENT,
    DOC_INTENT_PLANNING,
    SEMESTER_UNKNOWN,
)
from app.curriculum_engine.semester_detector import detect_semester
from app.curriculum_engine.week_detector import detect_week
from app.exam_engine.sources.base import SourceQuery
from app.exam_engine.sources.source_normalizer import NormalizedSample
from app.services.intents import normalize


# Strong planning signals — only these justify rejecting a sample as
# "non_exam_document". Generic words like "الفصل الدراسي" or "الوحدة"
# appear on real exam papers too, so they alone are not enough.
_STRONG_PLANNING_SIGNALS: tuple[str, ...] = (
    "heading:نواتج التعلم",
    "heading:نواتج تعلم",
    "heading:اهداف الدرس",
    "heading:التهيئه",
    "heading:الاجراءات",
    "heading:الواجب",
    "planning_cues:",
    "override:planning_skeleton",
    "override:week_marker+execution_word",
)


@dataclass(frozen=True)
class CurriculumDecision:
    """Outcome of running ``filter_by_curriculum``."""

    is_acceptable: bool
    reason: str = ""
    flags: tuple[str, ...] = ()


def filter_by_curriculum(
    sample: NormalizedSample,
    *,
    query: SourceQuery,
) -> CurriculumDecision:
    """Decide whether ``sample`` is curriculum-aligned with ``query``."""
    flags: list[str] = []
    reasons: list[str] = []

    blob = _sample_text_blob(sample)

    # ── Semester gate ─────────────────────────────────────────────────
    if query.semester:
        detected = detect_semester(text=blob).semester
        expected = _to_canonical_semester(query.semester)
        if detected != SEMESTER_UNKNOWN and expected and detected != expected:
            flags.append("semester_mismatch")
            reasons.append(
                f"الفصل في المصدر ({detected}) لا يطابق الفصل المطلوب ({expected})"
            )

    # ── Document intent gate ──────────────────────────────────────────
    intent_result = detect_document_intent(text=blob, title=sample.title)
    intent = intent_result.intent

    # Title-level short-circuit: an explicit assessment heading
    # ("اختبار قصير", "اختبار نهائي", "ورقه عمل", "اسئله الاختبار", ...)
    # in the title or blob means it's an exam, regardless of generic
    # planning vocabulary that might also appear in the header.
    norm_blob = normalize(blob)
    has_explicit_assessment_marker = any(
        h in norm_blob for h in ASSESSMENT_HEADINGS
    )

    if intent == DOC_INTENT_PLANNING and not has_explicit_assessment_marker:
        # Only reject when the planning signal is *strong* — generic
        # words like "الفصل الدراسي" or "الوحدة" appear on exam papers
        # too. We require at least one structural planning marker.
        if _has_strong_planning_signal(intent_result.signals):
            flags.append("non_exam_document")
            reasons.append("المستند يبدو خطة درس وليس اختبارًا")

    # Soft warning for unrelated intents (admin, certificate, …).
    elif intent not in (
        DOC_INTENT_ASSESSMENT, "unknown", DOC_INTENT_PLANNING,
    ):
        flags.append("unexpected_intent")
        reasons.append(f"نوع المستند غير متوقع: {intent}")

    # ── Week gate (advisory, never blocking) ──────────────────────────
    detected_week = detect_week(blob)
    requested_week = sample.meta.get("week")
    if detected_week and requested_week:
        try:
            req_w = int(requested_week)
            if abs(detected_week - req_w) > 4:
                flags.append("week_far")
                reasons.append(
                    f"أسبوع المصدر ({detected_week}) بعيد عن الطلب ({req_w})"
                )
        except (TypeError, ValueError):
            pass

    blocking = "semester_mismatch" in flags or "non_exam_document" in flags
    return CurriculumDecision(
        is_acceptable=not blocking,
        reason="؛ ".join(reasons) if reasons else "ok",
        flags=tuple(flags),
    )


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _sample_text_blob(sample: NormalizedSample) -> str:
    """Concatenate everything textual a curriculum detector might want."""
    parts: list[str] = [sample.title or ""]
    for q in sample.questions:
        if q.text:
            parts.append(q.text)
    meta = sample.meta or {}
    for key in ("semester", "unit", "lesson", "title"):
        v = meta.get(key)
        if v:
            parts.append(str(v))
    return "\n".join(parts)


def _has_strong_planning_signal(signals: tuple[str, ...]) -> bool:
    """True iff at least one structural planning cue is present."""
    for signal in signals:
        for marker in _STRONG_PLANNING_SIGNALS:
            if signal.startswith(marker) or signal == marker:
                return True
    return False


_SEMESTER_CANONICAL: dict[str, str] = {
    "الفصل الدراسي الأول": "first",
    "الفصل الأول": "first",
    "الأول": "first",
    "الفصل الدراسي الثاني": "second",
    "الفصل الثاني": "second",
    "الثاني": "second",
    "الفصل الدراسي الثالث": "third",
    "الفصل الثالث": "third",
    "الثالث": "third",
    "first": "first",
    "second": "second",
    "third": "third",
}


def _to_canonical_semester(value: str) -> str | None:
    if not value:
        return None
    cleaned = value.strip()
    return _SEMESTER_CANONICAL.get(cleaned)


__all__ = [
    "CurriculumDecision",
    "filter_by_curriculum",
]
