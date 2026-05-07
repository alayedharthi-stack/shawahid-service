"""
whatsapp_integration — thin bridge between the engine layer and webhook.py.

Phase-6 contract
================
This module contains helpers that COMBINE outputs from multiple engines
(review_engine, media_engine, whatsapp_messages) so the webhook only
needs a single function call at each integration point.

Hard rules:
    • No Playwright, no export_engine.
    • No direct DB queries — callers pass ORM results as plain iterables.
    • Every public function returns a plain string or None (never raises).
    • All functions are synchronous (webhook calls them in background tasks).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# ── 1. File received acknowledgment ───────────────────────────────────


def make_file_received_reply(evidence_type: str | None) -> str:
    """Return a short WhatsApp ack to send immediately when a media file
    arrives, before any AI processing begins.

    Delegates to ``whatsapp_messages.build_file_received_message``.
    """
    from app.services.whatsapp_messages import build_file_received_message
    return build_file_received_message(evidence_type or "document")


# ── 2. Smart save confirmation ─────────────────────────────────────────


def make_save_reply(
    *,
    ev_type: str,
    category: str,
    title: str | None = None,
    confidence: float | None = None,
    ai_raw: dict | None = None,
    is_duplicate: bool = False,
) -> str:
    """Build the post-save WhatsApp confirmation.

    Uses ``build_evidence_saved_smart`` which includes importance
    score, review hint when confidence is low, and duplicate note.

    Falls back to a plain confirmation on any error so saves never
    go silent.
    """
    from app.services.whatsapp_messages import (
        build_evidence_saved_smart,
        IMPORTANCE_STRONG, IMPORTANCE_MEDIUM, IMPORTANCE_SIMPLE,
    )
    from app.review_engine.schemas import LOW_CONFIDENCE_THRESHOLD

    importance = IMPORTANCE_MEDIUM
    needs_review = False

    if ai_raw:
        raw_imp = str(ai_raw.get("importance_score", "")).lower()
        if raw_imp in (IMPORTANCE_STRONG, "قوي", "strong"):
            importance = IMPORTANCE_STRONG
        elif raw_imp in (IMPORTANCE_SIMPLE, "بسيط", "simple"):
            importance = IMPORTANCE_SIMPLE

    if confidence is not None and confidence < LOW_CONFIDENCE_THRESHOLD:
        needs_review = True

    try:
        return build_evidence_saved_smart(
            ev_type=ev_type,
            category=category,
            title=title,
            importance=importance,
            needs_review=needs_review,
            is_duplicate=is_duplicate,
        )
    except Exception as exc:
        logger.warning("[WA INTEGRATION] make_save_reply fallback: %s", exc)
        return f"تم حفظ الشاهد ✅\n📁 {category}"


# ── 3. Uncertain classification note ──────────────────────────────────


def make_uncertain_note() -> str:
    """Short one-liner appended to save confirmation when confidence is low."""
    from app.services.whatsapp_messages import build_uncertain_classification_note
    return build_uncertain_classification_note()


# ── 4. Review link with session summary ───────────────────────────────


def make_review_link_reply(
    evidences,
    *,
    teacher_id: int,
    teacher_name: str | None,
    base_url: str,
    review_url: str,
) -> str:
    """Return a complete review message: session summary + link.

    Uses ``review_engine.build_review_session``,
    ``review_summary.build_summary_text``, and
    ``whatsapp_messages.build_review_link_message``.

    Returns a plain review-link message on any failure.
    """
    from app.services.whatsapp_messages import (
        build_review_ready_message,
        build_review_link_message,
    )
    from app.review_engine.review_service import build_review_session
    from app.review_engine.review_summary import build_summary_text

    try:
        session = build_review_session(
            evidences,
            teacher_id=teacher_id,
            teacher_name=teacher_name,
            base_url=base_url,
        )
        summary = build_summary_text(session)
        link_msg = build_review_link_message(review_url)
        return f"{summary}\n\n{link_msg}"
    except Exception as exc:
        logger.warning("[WA INTEGRATION] make_review_link_reply fallback: %s", exc)
        return build_review_link_message(review_url)


# ── 5. Pre-export review warning ──────────────────────────────────────


def make_pre_export_warning(
    evidences,
    *,
    teacher_id: int,
    teacher_name: str | None = None,
    base_url: str = "",
) -> str | None:
    """Return a warning message when there are duplicates or low-confidence
    items, or ``None`` when everything looks clean.

    The webhook sends this BEFORE the 2-button «مراجعة / تصدير» card
    so the teacher is aware before choosing.
    """
    from app.review_engine.review_service import build_review_session

    try:
        session = build_review_session(
            evidences,
            teacher_id=teacher_id,
            teacher_name=teacher_name,
            base_url=base_url,
        )
        if session.duplicates_count == 0 and session.low_confidence_count == 0:
            return None

        lines: list[str] = [
            "لديك بعض الشواهد التي قد تحتاج مراجعة ✏️",
            "",
        ]
        if session.duplicates_count:
            lines.append(f"⚠️ {session.duplicates_count} مكررة")
        if session.low_confidence_count:
            lines.append(f"📌 {session.low_confidence_count} تصنيفات ثقتها منخفضة")
        lines.append("")
        lines.append("يمكنك المتابعة بالتصدير أو مراجعتها أولًا.")
        return "\n".join(lines)

    except Exception as exc:
        logger.warning("[WA INTEGRATION] make_pre_export_warning fallback: %s", exc)
        return None


# ── 6. Batch summary ──────────────────────────────────────────────────


def make_batch_summary_reply(
    saves: list[tuple[str, str]],
) -> str | None:
    """Build a condensed multi-file summary when several evidences were
    saved in one pass.

    ``saves`` is a list of ``(evidence_type, category)`` tuples.
    Returns ``None`` when the list is empty or has only one item.
    """
    if len(saves) <= 1:
        return None

    from app.services.whatsapp_messages import build_batch_summary, BatchItem

    items = [BatchItem(category=cat) for _, cat in saves]
    try:
        return build_batch_summary(items)
    except Exception as exc:
        logger.warning("[WA INTEGRATION] make_batch_summary_reply fallback: %s", exc)
        return f"تم استلام {len(saves)} شواهد ✅"


# ── 7. Intent detection helper ────────────────────────────────────────


def resolve_text_intent(text: str | None):
    """Run the Phase-3 semantic intent detector and return the intent object.

    Returns ``None`` when text is empty. Never raises.
    """
    if not text:
        return None
    try:
        from app.services.intents import detect_intent
        return detect_intent(text)
    except Exception as exc:
        logger.warning("[WA INTEGRATION] resolve_text_intent failed: %s", exc)
        return None


# ── Phase-12: exam flow helpers ───────────────────────────────────────


def make_exam_flow_result(
    *,
    teacher_id: int,
    text: str | None,
    teacher_name: str | None = None,
    school_name: str | None = None,
    education_admin: str | None = None,
    region: str | None = None,
    teacher_subject: str | None = None,
    teacher_stage: str | None = None,
    teacher_grades: tuple[str, ...] = (),
    render_pdf: bool = True,
):
    """Run one turn of the exam conversation and return the result.

    Wrapper around ``app.exam_engine.exam_flow.handle_exam_request``
    that swallows unexpected exceptions and falls back to a generic
    failure message — keeps the webhook from ever crashing on the exam
    path. Returns an ``ExamFlowResult`` (see ``exam_flow``).
    """
    try:
        from app.exam_engine.exam_flow import (
            ExamFlowResult,
            STAGE_FAILED,
            handle_exam_request,
        )
    except Exception as exc:
        logger.error("[WA INTEGRATION] exam_flow import failed: %s", exc)
        return None

    try:
        return handle_exam_request(
            teacher_id=teacher_id,
            text=text,
            teacher_name=teacher_name,
            school_name=school_name,
            education_admin=education_admin,
            region=region,
            teacher_subject=teacher_subject,
            teacher_stage=teacher_stage,
            teacher_grades=teacher_grades,
            render_pdf=render_pdf,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[WA INTEGRATION] make_exam_flow_result failed: %s", exc,
        )
        return ExamFlowResult(
            stage=STAGE_FAILED,
            reply_text=(
                "حدث خطأ أثناء إنشاء الاختبار 🙏\n"
                "أعد إرسال طلبك أو حدد المادة والصف ونوع الاختبار."
            ),
        )


# ── 8. Name confirmation question ─────────────────────────────────────


def make_name_confirmation_question(new_name: str) -> str:
    """Return the name-confirmation WhatsApp message for a new audio-sourced
    name.

    Uses ``build_name_confirmation_question`` from ``whatsapp_messages``.
    """
    from app.services.whatsapp_messages import build_name_confirmation_question
    try:
        return build_name_confirmation_question(new_name)
    except Exception as exc:
        logger.warning("[WA INTEGRATION] make_name_confirmation_question fallback: %s", exc)
        return (
            f"هل تقصد اعتماد الاسم التالي رسميًا؟\n\"{new_name}\"\n\n"
            "✅ نعم، اعتمده\n✏️ لا، سأكتبه من جديد"
        )
