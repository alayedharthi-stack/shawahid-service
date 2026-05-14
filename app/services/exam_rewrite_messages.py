"""
exam_rewrite_messages — Phase 2/3 message + category helpers.
─────────────────────────────────────────────────────────────

Tiny, isolated module that owns the *exact* Arabic copy and the
detected-type → evidence-category mapping for the new exam / worksheet
choice prompt and the Phase-3 analysis result reply. Lives inside
``shawahid-service`` only.

Hard rules:
    • Pure functions: no DB, no network, no GPT.
    • The copy here is canonical — do not duplicate it elsewhere; the
      webhook and tests both import from this module so a wording
      change updates a single place.
    • Does NOT import from Nahla AI, campaigns, billing, subscriptions,
      catalog, coexistence, 360dialog, customer segmentation, or any
      shared cross-service logic.
"""
from __future__ import annotations

from pathlib import Path

from app.exam_rewrite.schemas import (
    EXAM_TYPE_ASSESSMENT,
    EXAM_TYPE_ASSIGNMENT,
    EXAM_TYPE_EXAM,
    EXAM_TYPE_WORKSHEET,
    StructuredExam,
)


# ──────────────────────────────────────────────────────────────────────
# Canonical Arabic copy
# ──────────────────────────────────────────────────────────────────────

# The choice prompt — matches the spec exactly so the teacher sees the
# same wording the product team approved.
CHOICE_PROMPT: str = (
    "وصلني ملف يبدو أنه اختبار أو ورقة عمل ✅\n"
    "هل تريد أن أحفظه كشاهد في ملف الشواهد، "
    "أم أعيد صياغته لك بكليشة المدرسة وبياناتك؟\n"
    "\n"
    "1️⃣ حفظه كشاهد\n"
    "2️⃣ إعادة صياغة الاختبار"
)


# Temporary placeholder while the rewrite engine is being built in
# later phases. The product team approved this exact wording.
REWRITE_PENDING_MESSAGE: str = (
    "تم اختيار إعادة صياغة الاختبار ✅\n"
    "سيتم تجهيز هذه الميزة في المرحلة التالية."
)


# Shown to the teacher when their reply to the choice prompt isn't
# clearly "1" or "2". The webhook may fall through to normal handling
# instead of sending this — use it where a gentle re-prompt is wanted.
CHOICE_CLARIFICATION_MESSAGE: str = (
    "حدد من فضلك:\n"
    "1️⃣ حفظه كشاهد\n"
    "2️⃣ إعادة صياغة الاختبار"
)


def build_choice_prompt() -> str:
    """Return the exact teacher-facing choice prompt."""
    return CHOICE_PROMPT


def build_rewrite_pending_message() -> str:
    """Return the temporary 'feature coming soon' message."""
    return REWRITE_PENDING_MESSAGE


def build_choice_clarification_message() -> str:
    """Return a short re-prompt when the teacher reply is unclear."""
    return CHOICE_CLARIFICATION_MESSAGE


# ──────────────────────────────────────────────────────────────────────
# Category + title helpers (for the save-as-evidence branch)
# ──────────────────────────────────────────────────────────────────────

# Maps the classifier ``detected_type`` to an existing evidence
# category from ``app.services.evidences.ALLOWED_CATEGORIES``. Keeping
# the mapping here means the webhook's choice-handler does not need to
# know which category strings the exporter expects.
_DETECTED_TYPE_TO_CATEGORY: dict[str, str] = {
    "exam": "اختبار",
    "worksheet": "ورقة عمل",
    "assignment": "واجب منزلي",
    "assessment": "تقويم",
}


def category_from_detected_type(detected_type: str | None) -> str:
    """Map ``detected_type`` → evidence category. Falls back to 'اختبار'.

    The fallback is intentional: by the time we reach this helper the
    Phase-1 classifier has already said the PDF is an exam-like
    document, so the worst case is still a sensible exam category.
    """
    if not detected_type:
        return "اختبار"
    return _DETECTED_TYPE_TO_CATEGORY.get(detected_type.strip().lower(), "اختبار")


def title_from_filename(filename: str | None, *, fallback: str = "اختبار") -> str:
    """Clean a PDF filename into a human-readable Arabic title.

    Strips the extension and turns ``_`` / ``-`` into spaces. If the
    cleaned title is empty or numeric-only we return ``fallback`` so
    the saved evidence row always has a meaningful title.
    """
    if not filename:
        return fallback
    stem = Path(str(filename)).stem
    cleaned = stem.replace("_", " ").replace("-", " ").strip()
    if not cleaned:
        return fallback
    if cleaned.replace(" ", "").isdigit():
        return fallback
    return cleaned


# ──────────────────────────────────────────────────────────────────────
# Phase 3 — analysis result replies
# ──────────────────────────────────────────────────────────────────────


# Map exam_type → user-facing Arabic label used in the analysis reply.
_EXAM_TYPE_LABELS: dict[str, str] = {
    EXAM_TYPE_EXAM:       "الاختبار",
    EXAM_TYPE_WORKSHEET:  "ورقة العمل",
    EXAM_TYPE_ASSIGNMENT: "الواجب",
    EXAM_TYPE_ASSESSMENT: "التقويم",
}


# Exact wording for the analysis-failure case (PDF unreadable / empty
# / no parsable questions). Approved by the product team — keep
# canonical so all callers send the same text.
REWRITE_ANALYSIS_FAILURE_MESSAGE: str = (
    "لم أستطع تحليل الاختبار بشكل كافٍ. ❗\n"
    "أعد إرسال الملف بجودة أوضح أو اكتب المادة والصف."
)


def build_rewrite_analysis_failure_message() -> str:
    """Return the canonical failure reply for the rewrite analysis."""
    return REWRITE_ANALYSIS_FAILURE_MESSAGE


def build_rewrite_analysis_success_message(
    structured: StructuredExam,
) -> str:
    """Compose the success reply summarising what the analyser found.

    The webhook calls this with a usable :class:`StructuredExam` (see
    :py:meth:`StructuredExam.is_usable`). Subject / grade fields fall
    back to a neutral Arabic placeholder when the analyser couldn't
    detect them — we don't silently invent the values.
    """
    type_label = _EXAM_TYPE_LABELS.get(structured.exam_type, "الاختبار")
    subject = (structured.subject or "غير محددة").strip()
    grade = (structured.grade or "غير محدد").strip()
    total_qs = structured.total_questions

    lines = [
        f"تم تحليل {type_label} بنجاح ✅",
        "اكتشفت:",
        f"• المادة: {subject}",
        f"• الصف: {grade}",
        f"• عدد الأسئلة: {total_qs}",
    ]
    if structured.total_score is not None:
        # Drop trailing ".0" for integer-valued scores.
        score_str = (
            f"{int(structured.total_score)}"
            if float(structured.total_score).is_integer()
            else f"{structured.total_score}"
        )
        lines.append(f"• مجموع الدرجات: {score_str}")
    lines.append("")
    lines.append(
        "المرحلة التالية ستكون إنشاء النسخة الجديدة بكليشة المدرسة."
    )
    return "\n".join(lines)


__all__ = [
    "CHOICE_PROMPT",
    "REWRITE_PENDING_MESSAGE",
    "CHOICE_CLARIFICATION_MESSAGE",
    "REWRITE_ANALYSIS_FAILURE_MESSAGE",
    "build_choice_prompt",
    "build_rewrite_pending_message",
    "build_choice_clarification_message",
    "build_rewrite_analysis_failure_message",
    "build_rewrite_analysis_success_message",
    "category_from_detected_type",
    "title_from_filename",
]
