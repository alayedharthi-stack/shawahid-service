"""
WhatsApp Cloud API webhooks + Moyasar payment webhook + payment success page.

GET  /webhook/whatsapp              — Meta webhook verification challenge
POST /webhook/whatsapp              — Incoming WhatsApp messages (GPT-driven)
POST /webhook/payment               — Moyasar payment webhook (activates subscription)
GET  /payment/success               — Redirect page after Moyasar checkout

Message flow:  WhatsApp inbound → ask_gpt() → GPTDecision → execute
GPT is the sole decision-maker. Code only executes what GPT decides.
"""
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.services.teachers import get_or_create_teacher, get_teacher_by_id, update_teacher
from app.services.evidences import create_evidence, get_teacher_evidences, set_enrichment_teacher_context, verify_evidence_in_export
from app.services.storage import download_and_save, detect_evidence_type, extract_urls, extract_pdf_text, extract_pdf_smart, generate_pdf_preview
from app.services.pdf_kind_classifier import classify_pdf_kind
from app.services import exam_rewrite_messages as _exam_rewrite_msgs
from app.conversation_engine import exam_rewrite_choice as _exam_choice
from app.exam_rewrite import analyze_exam_pdf as _analyze_exam_pdf
from app.services.deduplication import (
    is_exact_duplicate, find_near_duplicate_text,
    hash_text, hash_url,
    get_evidence_by_hash,
)
from app.services import transcribe as transcribe_svc
from app.services.subscriptions import (
    get_subscription_status,
    is_subscription_active,
    activate_subscription,
    get_payment_link,
    LAUNCH_AMOUNT_SAR,
)
from app.services.payments import (
    create_payment_attempt,
    update_payment_attempt_status,
    upsert_paid_payment_attempt,
)
from app.services import moyasar as moyasar_svc
from app.services.gpt_brain import ask_gpt, build_teacher_context
from app.services.whatsapp import (
    get_meta_media_url,
    send_whatsapp_message,
    send_whatsapp_button,
    send_export_options_buttons,
    send_pre_export_choice_buttons,
    send_review_offer,
    build_payment_link_message,
    build_payment_receipt_message,
    build_subscription_activated_message,
    build_file_saved_message,
)
from app.services import exporter as exporter_svc
from app.core.config import settings
from app.core.phone import normalize_phone
from app.services import whatsapp_integration as wa_integration
from app.services.tenant_guard import (
    build_inbound_debug_line,
    is_foreign_tenant,
    SERVICE_NAME as _TENANT_SERVICE_NAME,
)

router = APIRouter()
logger = logging.getLogger(__name__)

# Short in-memory session state. This is intentionally lightweight:
# it helps the conversational flow inside the current process without changing DB schema.
_LAST_PROFILE_UPDATES: dict[int, dict[str, str]] = {}
_PENDING_EXPORT_REQUESTS: set[int] = set()
# teacher_id → True when user just received the 2-button pre-export card (waiting for choice)
_AWAITING_EXPORT_CHOICE: set[int] = set()
# Pending name confirmation: teacher_id → name extracted from audio (needs user to confirm)
_PENDING_NAME_CONFIRMATION: dict[int, str] = {}
# Phase-6: teacher-provided category hint for the next evidence save.
# Set when teacher sends "هذه خطة" / "هذا اختبار" as a standalone text.
_PENDING_CATEGORY_HINT: dict[int, str] = {}


# ─── Arabic text normalization ────────────────────────────────────────────────

def _normalize_arabic(text: str) -> str:
    """
    Normalize Arabic text for intent matching:
    - Remove diacritics / tashkeel (harakat, shadda, sukun, etc.)
    - Normalize hamza variants (أ إ آ ٱ) → ا
    - Collapse whitespace and lowercase
    """
    # Remove tashkeel: harakat + tanwin + shadda + sukun + superscript alef
    text = re.sub(r'[\u064B-\u065F\u0670]', '', text)
    # Normalize all alef+hamza forms → bare alef
    text = re.sub(r'[أإآٱ]', 'ا', text)
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip().lower()
    return text


# ─── Export command detection ─────────────────────────────────────────────────

# All normalized forms that indicate the teacher wants to export.
_EXPORT_TRIGGERS: frozenset[str] = frozenset({
    # صدر core variants
    "صدر", "صدر الان", "صدر مباشرة", "صدر على اي حال",
    "صدر ملفي", "صدر الملف",
    # اصدر / تصدير
    "اصدر", "اصدر الان", "اصدر الملف", "اصدار الملف",
    "تصدير", "تصدير مباشر", "تصدير على اي حال", "تصدير الملف",
    # ابي / ابغى variants
    "ابي اصدر", "ابغى اصدر", "ابي الملف", "ابغى الملف",
    "ابي ملف الشواهد", "ابغى ملف الشواهد",
    "اريد الملف", "اريد اصدر", "اريد تصدير",
    # فعل + ملف
    "طلع الملف", "اطلع الملف", "اطلع لي الملف",
    "جهز الملف", "جهز لي الملف", "ارسل الملف",
    "ارسل ملف الشواهد", "ارسلي الملف",
    "حمل الملف", "اخرج الملف", "ابعث الملف",
    "اعطني الرابط", "اعطني الملف",
    "انشئ الملف", "انشي الملف", "انشئ لي الملف", "انشي لي الملف",
    "اعمل الملف", "اعمل لي الملف", "سو الملف", "سو لي الملف",
    # خلاص / تمام + صدر
    "خلاص صدر", "تمام صدر", "جاهز صدر",
    "انتهينا صدر", "لا تراجع صدر",
    "بدون مراجعة صدر", "بدون مراجعه صدر",
    "بدون مراجعة", "بدون مراجعه",
    # اكتفيت / جاهز
    "انا جاهز", "جاهز للتصدير", "انا مستعد",
})

# Substrings that – if found anywhere – reliably signal export intent
_EXPORT_SUBSTRINGS: tuple[str, ...] = (
    "صدر", "اصدر", "تصدير", "اصدار",
)


def is_export_command(text: str | None) -> bool:
    """
    Return True if text is any Arabic variant of an export/send-file request.
    Handles diacritics, hamza, shadda, and common colloquial spellings.
    """
    if not text:
        return False
    normalized = _normalize_arabic(text)
    if normalized in _EXPORT_TRIGGERS:
        return True
    for sub in _EXPORT_SUBSTRINGS:
        if sub in normalized:
            return True
    return False


# Deterministic, single-token export commands that MUST short-circuit
# the GPT router. These are the bare verbs the teacher reaches for
# without thinking — "صدر" / "تصدير" / "اصدر" — where GPT, without
# domain context, would naturally reply "ما الذي تقصده؟". In a
# teacher-portfolio bot they always mean "export my portfolio".
#
# The list is intentionally narrow: only commands whose normalised form
# is unambiguous in any reasonable conversation. Multi-word phrases
# already match through ``is_export_command`` further down.
_EXPORT_BARE_COMMANDS: frozenset[str] = frozenset({
    "صدر", "اصدر", "تصدير", "اصدار",
    "صدر الان", "اصدر الان", "تصدير الان",
})


def _is_positive_confirmation(text: str | None) -> bool:
    """Return True if text is an affirmative reply (yes/correct)."""
    if not text:
        return False
    n = _normalize_arabic(text)
    return n in {
        "نعم", "اي", "اي والله", "ايوه", "ايوا", "اه", "اهه",
        "صح", "صحيح", "هذا صح", "هذا صحيح", "نعم صح",
        "موافق", "تمام", "احفظه", "احفظ", "استمر",
        "نعم استمر", "يلا احفظه", "يلا", "زين", "سليم",
    }


def _is_negative_confirmation(text: str | None) -> bool:
    """Return True if text is a negative reply (no/incorrect)."""
    if not text:
        return False
    n = _normalize_arabic(text)
    return n in {
        "لا", "لا صح", "هذا غلط", "غلط", "خطا", "خطأ",
        "ليس صحيحا", "ليس صحيحًا", "لا تحفظ", "لا تحفظه",
        "مو صح", "مو صحيح", "لا استمر", "اعد", "اعد الكتابة",
    }

_PROFILE_FIELD_LABELS: dict[str, str] = {
    "name": "الاسم",
    "subject": "المادة",
    "stage": "المرحلة",
    "grades": "الصفوف",
    "school_name": "المدرسة",
    "principal_name": "مدير المدرسة",
    "region": "المنطقة",
    "education_admin": "إدارة التعليم",
}

# ─── Meta Webhook Verification (GET) ─────────────────────────────────────────

@router.get("/webhook/whatsapp")
async def verify_webhook(request: Request):
    params = request.query_params
    mode      = params.get("hub.mode")
    token     = params.get("hub.verify_token")
    challenge = params.get("hub.challenge", "")

    if mode == "subscribe" and token == settings.WHATSAPP_VERIFY_TOKEN:
        # Return challenge as plain text (Meta expects the raw string, not JSON)
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(challenge)

    logger.warning(
        "Webhook verification failed | mode=%r token_match=%s",
        mode, token == settings.WHATSAPP_VERIFY_TOKEN,
    )
    return {"error": "verification failed"}


# ─── Payload parsing helpers ─────────────────────────────────────────────────

def _parse_meta_payload(body: dict) -> dict | None:
    try:
        if body.get("object") != "whatsapp_business_account":
            return None
        entry = body["entry"][0]
        change = entry["changes"][0]
        value = change["value"]
        messages = value.get("messages")
        if not messages:
            return None

        msg = messages[0]
        from_phone = msg["from"]
        msg_type = msg.get("type", "text")

        # ── Meta tenant metadata (Phase-15 contamination guard) ──
        # Meta puts the business number that *received* the inbound
        # message into ``value.metadata.phone_number_id``. We compare
        # this against ``settings.WHATSAPP_PHONE_NUMBER_ID`` later so
        # messages destined for any other business (Nahla / honey
        # store / etc.) are dropped before any reply can leak.
        metadata = value.get("metadata") or {}
        phone_number_id = metadata.get("phone_number_id")
        display_phone_number = metadata.get("display_phone_number")
        waba_id = entry.get("id")

        contacts = value.get("contacts") or []
        contact_name = None
        contact_wa_id = None
        if contacts:
            contact_name = (contacts[0].get("profile") or {}).get("name")
            contact_wa_id = contacts[0].get("wa_id")

        message_id = msg.get("id")

        text: str | None = None
        media_id: str | None = None
        mime_type: str | None = None
        file_name: str | None = None

        if msg_type == "text":
            text = msg["text"]["body"]
        elif msg_type == "image":
            media_id = msg["image"]["id"]
            mime_type = msg["image"].get("mime_type", "image/jpeg")
            caption = msg["image"].get("caption")
            if caption:
                text = caption
        elif msg_type == "document":
            media_id = msg["document"]["id"]
            mime_type = msg["document"].get("mime_type", "application/octet-stream")
            file_name = msg["document"].get("filename")
            caption = msg["document"].get("caption")
            if caption:
                text = caption
        elif msg_type == "video":
            media_id = msg["video"]["id"]
            mime_type = msg["video"].get("mime_type", "video/mp4")
            caption = msg["video"].get("caption")
            if caption:
                text = caption
        elif msg_type == "audio":
            media_id = msg["audio"]["id"]
            mime_type = msg["audio"].get("mime_type", "audio/ogg")
            # WhatsApp distinguishes voice_note from audio file via the "voice" flag
            # We use "voice" as the msg_type for voice notes, "audio" for music/files
            if msg["audio"].get("voice"):
                msg_type = "voice"
        elif msg_type == "interactive":
            interactive = msg.get("interactive", {})
            if interactive.get("type") == "button_reply":
                reply = interactive.get("button_reply", {})
                # Prefer stable id (export_full/export_smart/export_short), fallback to title.
                text = reply.get("id") or reply.get("title")
                msg_type = "text"
            else:
                logger.info("Unsupported interactive type: %s — ignoring", interactive.get("type"))
                return None
        else:
            logger.info("Unsupported Meta message type: %s — ignoring", msg_type)
            return None

        return {
            "from_phone": from_phone,
            "msg_type":   msg_type,       # text | image | document | video | audio | voice
            "text":       text,
            "media_id":   media_id,
            "mime_type":  mime_type,
            "file_name":  file_name,
            # ── Tenant identity (used by the contamination guard) ──
            "phone_number_id":      phone_number_id,
            "display_phone_number": display_phone_number,
            "waba_id":              waba_id,
            "contact_name":         contact_name,
            "contact_wa_id":        contact_wa_id,
            "message_id":           message_id,
        }
    except (KeyError, IndexError, TypeError) as exc:
        logger.debug("Meta payload parse error: %s", exc)
        return None


def _parse_simple_payload(body: dict) -> dict | None:
    if "from_phone" not in body:
        return None
    return {
        "from_phone": body["from_phone"],
        "msg_type":   body.get("msg_type", "text"),
        "text":       body.get("text"),
        "media_id":   body.get("media_id"),
        "mime_type":  body.get("mime_type"),
        "file_name":  body.get("file_name"),
        "media_url":  body.get("media_url"),
    }


# ─── Moyasar helper — create invoice and save attempt ───────────────────────

async def _create_moyasar_link(
    db: Session,
    teacher_id: int,
    teacher_name: str = "",
    teacher_phone: str = "",
) -> str:
    """
    Creates a Moyasar invoice with full Shawahid metadata, saves a
    payment_attempt record, and returns the payment URL.
    Falls back to the static PAYMENT_LINK_TEMPLATE when Moyasar is not configured.
    """
    if not settings.MOYASAR_SECRET_KEY:
        logger.warning("MOYASAR_SECRET_KEY not set — using fallback payment link")
        return get_payment_link(teacher_id)

    result = await moyasar_svc.create_invoice(
        service="shawahid",
        teacher_id=teacher_id,
        teacher_phone=teacher_phone,
        teacher_name=teacher_name,
    )
    create_payment_attempt(
        db=db,
        teacher_id=teacher_id,
        provider_payment_id=result["provider_payment_id"],
        payment_url=result["payment_url"],
        raw_response=result["raw_response"],
        metadata=result["metadata"],
    )
    logger.info("[PAYMENT LINK SENT] teacher_id=%d channel=whatsapp", teacher_id)
    return result["payment_url"]


# ─── GPT background retry ────────────────────────────────────────────────────

async def _gpt_retry_and_reply(
    teacher_id: int,
    phone: str,
    text: str | None,
    teacher_context: str,
    storage_path: str | None,
    image_url: str | None,
    mime_type: str | None,
    file_name: str | None,
) -> None:
    """
    Called as a background task when the initial GPT call fails completely.
    Waits a few seconds, retries ask_gpt(), then sends the real reply.
    Skips evidence saving (user can resend to save).
    """
    import asyncio as _asyncio
    from app.services.gpt_brain import ask_gpt as _ask_gpt

    await _asyncio.sleep(4)
    logger.info("[RETRY] Background GPT retry for teacher_id=%d", teacher_id)

    decision = await _ask_gpt(
        text=text,
        teacher_context=teacher_context,
        storage_path=storage_path,
        image_url=image_url,
        mime_type=mime_type,
        file_name=file_name,
    )

    if decision["intent"] != "failure":
        await send_whatsapp_message(phone, decision["reply"], teacher_id=teacher_id)
        logger.info(
            "[RETRY] Background retry succeeded for teacher_id=%d intent=%s",
            teacher_id, decision["intent"],
        )
    else:
        logger.warning("[RETRY] Background retry also failed for teacher_id=%d", teacher_id)


_WELCOME_SHORT_MSG = (
    "مرحبًا بك في شواهد AI 👋\n"
    "أرسل صورة، ملف، تسجيل صوتي أو اكتب وصف الشاهد… وسأرتبه لك تلقائيًا."
)

_VOICE_HINT_MSG = (
    "تلميح 🎙️\n"
    "يمكنك أيضًا إرسال مقطع صوتي، وسأفهمه وأحوّله إلى وصف شاهد مرتب."
)

_MEDIA_NO_DESCRIPTION_HINT_MSG = (
    "وصلتني الوسائط ✅\n"
    "اكتب أو سجّل بصوتك وصفًا بسيطًا للشاهد، وسأوثقه لك."
)

_FIRST_VOICE_SUCCESS_MSG = (
    "فهمت المقطع الصوتي ✅\n"
    "وسأستخدمه في صياغة الشاهد وتنظيم ملفك."
)


async def _send_exam_download_button(
    *,
    teacher_phone: str,
    teacher_id: int,
    exam_id: str,
    download_url: str,
    subject: str | None = None,
    grade: str | None = None,
    exam_type: str | None = None,
    exam=None,
) -> None:
    """Phase-13: send a CTA-URL button so the teacher can download the
    exam from any device.

    Behaviour:
      1. Try the WhatsApp interactive ``cta_url`` button — same UX
         the portfolio download / payment links already use.
      2. If the API call fails (out of session, credentials missing,
         API error) fall back to a clean text message containing the
         URL on its own line.

    Never raises; only logs.
    """
    from app.exam_engine.messages import (
        build_exam_download_button_body,
        build_exam_download_text_fallback,
    )
    from app.services.whatsapp import send_whatsapp_button

    body = build_exam_download_button_body(exam)
    button_label = "تحميل الاختبار 📄"

    sent = False
    try:
        sent = await send_whatsapp_button(
            teacher_phone,
            body_text=body,
            button_label=button_label,
            url=download_url,
            teacher_id=teacher_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[EXAM DOWNLOAD BUTTON FAILED] teacher_id=%d exam_id=%s err=%s",
            teacher_id, exam_id, exc,
        )
        sent = False

    if sent:
        logger.info(
            "[EXAM DOWNLOAD BUTTON SENT] teacher_id=%d exam_id=%s",
            teacher_id, exam_id,
        )
        return

    # Fallback: plain text with the link on its own line so iOS / Android
    # auto-link it. We deliberately keep it short and friendly.
    fallback = build_exam_download_text_fallback(
        download_url=download_url,
        subject=subject,
        grade=grade,
        exam_type=exam_type,
    )
    await send_whatsapp_message(
        teacher_phone,
        fallback,
        teacher_id=teacher_id,
        context="exam_download_fallback",
    )


# Phase-12 compat shim — older call-sites still import this name.
async def _send_exam_pdf(  # noqa: D401  (kept for backwards-compat)
    *,
    teacher_phone: str,
    teacher_id: int,
    pdf_path: str | None,
    exam,
) -> None:
    """Compatibility wrapper kept for code that hasn't moved to the
    button-first flow yet. Uses the conversation state to find the
    download URL the new path stored.
    """
    if not pdf_path:
        return
    try:
        from app.api.exam_downloads import build_exam_download_url
        from app.conversation_engine.exam_state import get_exam_state
        st = get_exam_state(teacher_id)
        exam_id = (
            getattr(exam, "exam_id", None)
            or st.last_exam_id
            or "unknown"
        )
        download_url = (
            st.last_exam_download_url
            or build_exam_download_url(teacher_id=teacher_id, exam_id=exam_id)
        )
        await _send_exam_download_button(
            teacher_phone=teacher_phone,
            teacher_id=teacher_id,
            exam_id=exam_id,
            download_url=download_url,
            subject=st.last_exam_subject,
            grade=st.last_exam_grade,
            exam_type=st.last_exam_type,
            exam=exam,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[EXAM PDF SHIM] err teacher_id=%d: %s", teacher_id, exc)


async def _send_welcome_message(phone: str, teacher_id: int) -> None:
    """
    Send one-time short onboarding welcome. Runs as a background task after the first reply.
    """
    try:
        await send_whatsapp_message(phone, _WELCOME_SHORT_MSG, teacher_id=teacher_id, context="onboarding_welcome")
        logger.info("[ONBOARDING] short welcome sent teacher_id=%d", teacher_id)
    except Exception as exc:
        logger.warning("[ONBOARDING] failed teacher_id=%d: %s", teacher_id, exc)


# ─── Export-mode selection: shared helper ────────────────────────────────────
#
# Stage 3 of the export flow: teacher picked كامل / ذكي / مختصر. The
# helper runs the actual export (paywall check, create export record,
# kick off the background generator) and returns the response dict the
# webhook ``return``s.
#
# Both the deterministic pre-router short-circuit and the legacy
# ``selected_export_mode`` block call this so the behaviour stays
# identical regardless of how the mode was matched.

async def _handle_export_mode_selection(
    *,
    teacher,
    db: Session,
    background_tasks: BackgroundTasks,
    sub_active: bool,
    sub_info: dict | None,
    selected_export_mode: str,
    text: str | None,
    via: str,
) -> dict:
    """Start the export run for ``selected_export_mode`` (full/smart/elite).

    ``via`` is logged so we can tell whether the trigger came from
    the pre-router short-circuit or the legacy backstop.
    """
    if teacher.id not in _PENDING_EXPORT_REQUESTS:
        # The teacher tapped a mode button without going through the
        # 2-button card first (or a previous request silently reset
        # state). Treat it as an instant export-and-mode in one step.
        logger.info(
            "[EXPORT MODE DIRECT] teacher_id=%d mode=%s via=%s",
            teacher.id, selected_export_mode, via,
        )
        _PENDING_EXPORT_REQUESTS.add(teacher.id)

    # Critical: pressing a mode button must NEVER re-prompt the
    # مراجعة/تصدير card. We clear both wait-states up-front.
    _AWAITING_EXPORT_CHOICE.discard(teacher.id)

    logger.info(
        "[EXPORT MODE_SELECTED] teacher_id=%d mode=%s via=%s sub_status=%s",
        teacher.id, selected_export_mode, via,
        (sub_info or {}).get("status", "unknown"),
    )
    _PENDING_EXPORT_REQUESTS.discard(teacher.id)

    if not sub_active:
        try:
            payment_url = await _create_moyasar_link(
                db, teacher.id, teacher.name or "", teacher.phone or "",
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Moyasar invoice creation failed: %s", exc)
            payment_url = get_payment_link(teacher.id)
        background_tasks.add_task(
            send_whatsapp_message,
            teacher.phone,
            build_payment_link_message(payment_url, teacher.name or ""),
            teacher_id=teacher.id,
            context="payment_link",
        )
        return {
            "ok": False, "teacher_id": teacher.id,
            "reason": "subscription_required",
        }

    export_record = exporter_svc.create_export_record(db, teacher.id)
    logger.info(
        "[EXPORT STARTED] teacher_id=%d mode=%s export_id=%d via=%s",
        teacher.id, selected_export_mode, export_record.id, via,
    )
    background_tasks.add_task(
        send_whatsapp_message,
        teacher.phone,
        _export_start_message(teacher.name),
        teacher_id=teacher.id,
        context="export_started",
    )
    background_tasks.add_task(
        exporter_svc.run_export_background,
        teacher_id=teacher.id,
        export_id=export_record.id,
        export_mode=selected_export_mode,
    )
    return {
        "ok": True,
        "teacher_id": teacher.id,
        "intent": "export_started",
        "export_id": export_record.id,
        "export_mode": selected_export_mode,
        "via": via,
    }


# ─── Pre-export choice card: shared helper ───────────────────────────────────
#
# Both the high-priority deterministic short-circuit (run BEFORE the
# GPT router) and the legacy ``is_export_command`` block call this so
# the behaviour stays identical regardless of which path detects the
# intent. Returns the response dict the webhook should ``return``.

async def _run_pre_export_choice_flow(
    *,
    teacher,
    db: Session,
    background_tasks: BackgroundTasks,
    sub_active: bool,
    text: str | None,
    via: str,
) -> dict:
    """Show the مراجعة الملف / تصدير الآن 2-button card.

    ``via`` goes into log lines so we can tell whether the trigger was
    a single-word bare command, the substring matcher, or the GPT
    router (kept here as a single source of truth).

    Re-prompt semantics
    -------------------
    Reaching this helper means the user *intentionally* re-typed an
    export command (button presses for the card are handled higher up
    by deterministic short-circuits and never call this helper). If
    the teacher is already in ``_AWAITING_EXPORT_CHOICE``, that almost
    always means they didn't see the previous card or it scrolled
    away — silently swallowing the retry leaves them stranded. We log
    ``[EXPORT CHOICE_REPROMPTED]`` and re-send the card instead.
    """
    if teacher.id in _AWAITING_EXPORT_CHOICE:
        logger.info(
            "[EXPORT CHOICE_REPROMPTED] teacher_id=%d "
            "msg=%r already_awaiting=True via=%s",
            teacher.id, (text or "")[:40], via,
        )
        # Clear and fall through; the helper will re-add it below.
        _AWAITING_EXPORT_CHOICE.discard(teacher.id)

    if not sub_active:
        try:
            payment_url = await _create_moyasar_link(
                db, teacher.id, teacher.name or "", teacher.phone or "",
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Moyasar invoice creation failed: %s", exc)
            payment_url = get_payment_link(teacher.id)
        background_tasks.add_task(
            send_whatsapp_message,
            teacher.phone,
            build_payment_link_message(payment_url, teacher.name or ""),
            teacher_id=teacher.id,
            context="payment_link",
        )
        return {
            "ok": False, "teacher_id": teacher.id,
            "reason": "subscription_required",
        }

    from app.services.teachers import get_or_create_review_token
    review_token = get_or_create_review_token(db, teacher)
    review_url = f"{settings.effective_base_url}/review/{review_token}"

    warning = wa_integration.make_pre_export_warning(
        get_teacher_evidences(db, teacher.id),
        teacher_id=teacher.id,
        teacher_name=teacher.name,
        base_url=settings.effective_base_url,
    )
    if warning:
        background_tasks.add_task(
            send_whatsapp_message, teacher.phone, warning,
            teacher_id=teacher.id, context="pre_export_warning",
        )

    _AWAITING_EXPORT_CHOICE.add(teacher.id)
    logger.info(
        "[EXPORT DECISION PROMPTED] teacher_id=%d via=%s",
        teacher.id, via,
    )
    background_tasks.add_task(
        send_pre_export_choice_buttons,
        teacher.phone, review_url, teacher_id=teacher.id,
    )
    return {
        "ok": True, "teacher_id": teacher.id,
        "intent": "pre_export_choice_offered",
        "via": via,
    }


# ─── Phase-13: GPT-First Router dispatcher ────────────────────────────────────

async def _route_via_gpt_router(
    *,
    message: str,
    teacher,
    db: Session,
    background_tasks: BackgroundTasks,
    has_media: bool,
    media_type: str | None,
    has_transcript: bool,
    sub_active: bool,
):
    """Run the GPT-first router and dispatch the chosen action.

    Returns ``(decision, response_dict_or_None)``.

    * ``response_dict`` is non-None when the webhook should return immediately
      (chat_reply / ask_clarification / delete_or_edit / update_profile /
      create_exam / export_portfolio / review_portfolio).
    * Returns ``None`` for ``save_evidence`` and ``unknown`` — caller must
      continue to the legacy ``ask_gpt`` flow.

    The router itself does not write to the DB or send messages; this helper
    is the single integration point that turns a decision into side effects.
    """
    from app.services.gpt_router import (
        ACTION_ASK_CLARIFICATION,
        ACTION_CHAT_REPLY,
        ACTION_CREATE_EXAM,
        ACTION_DELETE_OR_EDIT,
        ACTION_EXPORT_PORTFOLIO,
        ACTION_REVIEW_PORTFOLIO,
        ACTION_SEND_LAST_EXAM,
        ACTION_UPDATE_PROFILE,
        RouterContext,
        decide_next_action,
    )

    # Best-effort: detect whether we're mid-exam-flow so GPT can lean toward
    # create_exam for terse follow-ups like "اختبار قصير".
    in_exam_flow = False
    try:
        from app.conversation_engine.exam_state import get_exam_state as _ges
        in_exam_flow = bool(_ges(teacher.id).pending_fields)
    except Exception:  # noqa: BLE001
        in_exam_flow = False

    decision = await decide_next_action(
        message,
        RouterContext(
            teacher_id=teacher.id,
            teacher_name=teacher.name,
            teacher_subject=teacher.subject,
            teacher_stage=teacher.stage,
            teacher_grades=teacher.grades,
            teacher_school=teacher.school_name,
            teacher_region=teacher.region,
            teacher_education_admin=teacher.education_admin,
            has_media=has_media,
            media_type=media_type,
            has_transcript=has_transcript,
            in_exam_flow=in_exam_flow,
            awaiting_export_choice=teacher.id in _AWAITING_EXPORT_CHOICE,
            pending_name_confirmation=teacher.id in _PENDING_NAME_CONFIRMATION,
            pending_category_hint=_PENDING_CATEGORY_HINT.get(teacher.id),
        ),
    )

    logger.info(
        "[GPT ROUTER] teacher_id=%d action=%s conf=%.2f source=%s save=%s reply=%r",
        teacher.id, decision.action, decision.confidence,
        decision.source, decision.should_save_evidence,
        (decision.reply_text or "")[:60],
    )

    # ── Pure replies: send and stop ───────────────────────────────────────
    if decision.action == ACTION_CHAT_REPLY:
        background_tasks.add_task(
            send_whatsapp_message, teacher.phone,
            decision.reply_text or "تمام 🌿",
            teacher_id=teacher.id, context="router_chat_reply",
        )
        return decision, {
            "ok": True, "teacher_id": teacher.id,
            "intent": "chat_reply", "router_action": decision.action,
        }

    if decision.action == ACTION_ASK_CLARIFICATION:
        msg = (
            decision.clarification_question
            or decision.reply_text
            or "هل يمكنك توضيح طلبك أكثر؟ 🌿"
        )
        background_tasks.add_task(
            send_whatsapp_message, teacher.phone, msg,
            teacher_id=teacher.id, context="router_ask_clarification",
        )
        return decision, {
            "ok": True, "teacher_id": teacher.id,
            "intent": "ask_clarification", "router_action": decision.action,
        }

    if decision.action == ACTION_DELETE_OR_EDIT:
        # Don't auto-delete from a freeform routed message — point the
        # teacher to the review page (deterministic and undo-friendly).
        reply = (
            decision.reply_text
            or "يمكنك تعديل/حذف أي شاهد من رابط مراجعة الملف ✏️"
        )
        background_tasks.add_task(
            send_whatsapp_message, teacher.phone, reply,
            teacher_id=teacher.id, context="router_delete_or_edit",
        )
        return decision, {
            "ok": True, "teacher_id": teacher.id,
            "intent": "delete_or_edit", "router_action": decision.action,
        }

    # ── Profile update: apply + reply ─────────────────────────────────────
    if decision.action == ACTION_UPDATE_PROFILE:
        cleaned = _clean_profile_update(decision.profile_update)

        # Voice-sourced names need confirmation (Whisper mishears Arabic).
        pending_name: str | None = None
        if has_transcript and "name" in cleaned:
            new_name = cleaned.pop("name")
            existing = (teacher.name or "").strip()
            if existing != new_name:
                _PENDING_NAME_CONFIRMATION[teacher.id] = new_name
                pending_name = new_name
            else:
                cleaned["name"] = new_name

        if cleaned:
            update_teacher(db, teacher, cleaned)
            _LAST_PROFILE_UPDATES[teacher.id] = cleaned
            logger.info(
                "[ROUTER PROFILE UPDATED] teacher_id=%d fields=%s",
                teacher.id, list(cleaned.keys()),
            )

        if pending_name:
            reply = wa_integration.make_name_confirmation_question(pending_name)
        else:
            reply = decision.reply_text or _format_profile_update_reply(cleaned)
        background_tasks.add_task(
            send_whatsapp_message, teacher.phone, reply,
            teacher_id=teacher.id, context="router_update_profile",
        )
        return decision, {
            "ok": True, "teacher_id": teacher.id,
            "intent": "update_profile", "router_action": decision.action,
            "fields": list(cleaned.keys()),
        }

    # ── Exam creation: invoke exam_flow on the routed text/transcript ─────
    if decision.action == ACTION_CREATE_EXAM:
        grades_tuple: tuple[str, ...] = ()
        if teacher.grades:
            if isinstance(teacher.grades, (list, tuple)):
                grades_tuple = tuple(str(g) for g in teacher.grades if g)
            else:
                grades_tuple = (str(teacher.grades),)

        exam_result = wa_integration.make_exam_flow_result(
            teacher_id=teacher.id,
            text=message,
            teacher_name=teacher.name,
            school_name=teacher.school_name,
            education_admin=teacher.education_admin,
            region=teacher.region,
            teacher_subject=teacher.subject,
            teacher_stage=teacher.stage,
            teacher_grades=grades_tuple,
            render_pdf=True,
        )

        if exam_result is not None:
            for pm in (exam_result.progress_messages or []):
                background_tasks.add_task(
                    send_whatsapp_message, teacher.phone, pm,
                    teacher_id=teacher.id, context="exam_progress",
                )
            background_tasks.add_task(
                send_whatsapp_message, teacher.phone, exam_result.reply_text,
                teacher_id=teacher.id, context=f"exam_{exam_result.stage}",
            )
            if exam_result.is_ready and exam_result.exam is not None:
                # Build the public download URL and persist it on
                # ExamConversationState so follow-up "أين الرابط؟"
                # questions can be answered without regenerating.
                from app.api.exam_downloads import build_exam_download_url
                from app.conversation_engine.exam_state import (
                    update_last_exam_download_url,
                )
                exam_id = exam_result.exam.exam_id
                download_url = build_exam_download_url(
                    teacher_id=teacher.id, exam_id=exam_id,
                )
                update_last_exam_download_url(
                    teacher.id, download_url=download_url,
                )
                background_tasks.add_task(
                    _send_exam_download_button,
                    teacher_phone=teacher.phone,
                    teacher_id=teacher.id,
                    exam_id=exam_id,
                    download_url=download_url,
                    subject=exam_result.exam.profile.subject,
                    grade=exam_result.exam.profile.grade,
                    exam_type=exam_result.exam.profile.exam_type,
                    exam=exam_result.exam,
                )
            return decision, {
                "ok": True, "teacher_id": teacher.id,
                "intent": "exam_flow", "router_action": decision.action,
                "exam_stage": exam_result.stage,
            }
        # exam_flow unavailable — let caller fall through.
        return decision, None

    # ── Send the LAST generated exam (no new generation) ──────────────────
    if decision.action == ACTION_SEND_LAST_EXAM:
        from app.conversation_engine.exam_state import get_exam_state
        from app.exam_engine.messages import build_no_last_exam_message

        st = get_exam_state(teacher.id)
        if st.last_exam_id and st.last_exam_download_url:
            background_tasks.add_task(
                _send_exam_download_button,
                teacher_phone=teacher.phone,
                teacher_id=teacher.id,
                exam_id=st.last_exam_id,
                download_url=st.last_exam_download_url,
                subject=st.last_exam_subject,
                grade=st.last_exam_grade,
                exam_type=st.last_exam_type,
                exam=None,
            )
            return decision, {
                "ok": True, "teacher_id": teacher.id,
                "intent": "send_last_exam",
                "router_action": decision.action,
                "exam_id": st.last_exam_id,
            }

        # No cached exam → friendly fallback prompting a new generation.
        background_tasks.add_task(
            send_whatsapp_message, teacher.phone,
            build_no_last_exam_message(),
            teacher_id=teacher.id, context="send_last_exam_fallback",
        )
        return decision, {
            "ok": True, "teacher_id": teacher.id,
            "intent": "send_last_exam_missing",
            "router_action": decision.action,
        }

    # ── Export portfolio: same shortcut as is_export_command ──────────────
    if decision.action == ACTION_EXPORT_PORTFOLIO:
        # Re-prompt (not block) when teacher is already in the
        # مراجعة/تصدير awaiting state. The deterministic button
        # short-circuits higher up handle button-id replays, so any
        # path that reaches here is a fresh user-typed export
        # request — re-sending the card is what the user expects.
        if teacher.id in _AWAITING_EXPORT_CHOICE:
            logger.info(
                "[EXPORT CHOICE_REPROMPTED] teacher_id=%d "
                "msg=%r already_awaiting=True via=router",
                teacher.id, (message or "")[:40],
            )
            _AWAITING_EXPORT_CHOICE.discard(teacher.id)

        if not sub_active:
            try:
                payment_url = await _create_moyasar_link(
                    db, teacher.id, teacher.name or "", teacher.phone or ""
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("Moyasar invoice creation failed: %s", exc)
                payment_url = get_payment_link(teacher.id)
            background_tasks.add_task(
                send_whatsapp_message, teacher.phone,
                build_payment_link_message(payment_url, teacher.name or ""),
                teacher_id=teacher.id, context="payment_link",
            )
            return decision, {
                "ok": False, "teacher_id": teacher.id,
                "intent": "export_portfolio",
                "router_action": decision.action,
                "reason": "subscription_required",
            }

        from app.services.teachers import get_or_create_review_token
        review_token = get_or_create_review_token(db, teacher)
        review_url = f"{settings.effective_base_url}/review/{review_token}"
        warning = wa_integration.make_pre_export_warning(
            get_teacher_evidences(db, teacher.id),
            teacher_id=teacher.id, teacher_name=teacher.name,
            base_url=settings.effective_base_url,
        )
        if warning:
            background_tasks.add_task(
                send_whatsapp_message, teacher.phone, warning,
                teacher_id=teacher.id, context="pre_export_warning",
            )
        _AWAITING_EXPORT_CHOICE.add(teacher.id)
        logger.info(
            "[EXPORT DECISION PROMPTED] teacher_id=%d via=router", teacher.id,
        )
        background_tasks.add_task(
            send_pre_export_choice_buttons,
            teacher.phone, review_url, teacher_id=teacher.id,
        )
        return decision, {
            "ok": True, "teacher_id": teacher.id,
            "intent": "export_portfolio",
            "router_action": decision.action,
            "awaiting_review_or_export": True,
        }

    # ── Review portfolio: send signed review link ─────────────────────────
    if decision.action == ACTION_REVIEW_PORTFOLIO:
        from app.services.teachers import get_or_create_review_token
        rt = get_or_create_review_token(db, teacher)
        rv = f"{settings.effective_base_url}/review/{rt}"
        evidences = get_teacher_evidences(db, teacher.id)
        review_reply = wa_integration.make_review_link_reply(
            evidences,
            teacher_id=teacher.id, teacher_name=teacher.name,
            base_url=settings.effective_base_url, review_url=rv,
        )
        background_tasks.add_task(
            send_whatsapp_message, teacher.phone, review_reply,
            teacher_id=teacher.id, context="router_review",
        )
        return decision, {
            "ok": True, "teacher_id": teacher.id,
            "intent": "review_portfolio",
            "router_action": decision.action,
        }

    # save_evidence | unknown → caller continues to ask_gpt
    return decision, None


def _parse_export_mode(text: str | None) -> str | None:
    """Parse teacher's export mode selection with Arabic normalization."""
    if not text:
        return None
    n = _normalize_arabic(text)
    if n in ("1", "١", "كامل", "كاملة", "كل الشواهد", "full", "export_full", "full_export"):
        return "full"
    if n in ("2", "٢", "ذكي", "ذكية", "ذكى", "smart", "export_smart"):
        return "smart"
    if n in ("3", "٣", "مختصر", "مختصرة", "مختصر جدا", "مختصر جدًا", "elite", "export_short"):
        return "elite"
    return None


def _export_mode_question() -> str:
    return (
        "كيف تحب ملفك؟ 📘\n\n"
        "1️⃣ كامل: كل الشواهد والوسائط\n"
        "2️⃣ ذكي: أفضل الشواهد مع اختصار جميل\n"
        "3️⃣ مختصر جدًا: أقوى الشواهد فقط\n\n"
        "اكتب الرقم فقط."
    )


def _export_start_message(teacher_name: str | None) -> str:
    display_name = teacher_name or "أستاذ"
    return (
        f"يا {display_name}، جاري إنشاء ملف شواهدك الآن ⏳\n"
        "سيصلك رابط التحميل فور الانتهاء."
    )


def _clean_profile_update(data: dict | None) -> dict[str, str]:
    if not data:
        return {}
    clean: dict[str, str] = {}
    for key, value in data.items():
        if key not in _PROFILE_FIELD_LABELS or value is None:
            continue
        if isinstance(value, (list, tuple)):
            text_value = "، ".join(str(v).strip() for v in value if str(v).strip())
        else:
            text_value = str(value).strip()
        if text_value and text_value.lower() not in ("null", "none", "undefined"):
            clean[key] = text_value
    return clean


def _format_profile_update_reply(update: dict[str, str]) -> str:
    if not update:
        return "لم أجد تحديثًا جديدًا في بيانات ملفك حتى الآن."
    parts = [
        f"{_PROFILE_FIELD_LABELS.get(key, key)} {value}"
        for key, value in update.items()
    ]
    return "نعم، تم تحديث بيانات ملفك: " + "، و".join(parts) + "."


def _is_profile_update_followup(text: str | None) -> bool:
    if not text:
        return False
    t = text.strip().replace("؟", "").replace("?", "")
    return t in {
        "هل حدثتها", "حدثتها", "تم تحديثها", "هل حفظتها",
        "حفظتها", "حدثت البيانات", "هل تم تحديث البيانات",
    }


def _is_creator_question(text: str | None) -> bool:
    if not text:
        return False
    t = text.strip().replace("؟", "").replace("?", "")
    triggers = ("من صنعك", "من طورك", "من مؤسسك", "من مطورك", "مين صنعك", "مين طورك")
    return any(trigger in t for trigger in triggers)


# ── PDF category inference from filename ──────────────────────────────────────
_PDF_CATEGORY_HINTS: list[tuple[tuple[str, ...], str]] = [
    # Most-specific rules first to avoid false matches from generic words.
    # ── Assessment ──────────────────────────────────────────────────────────────
    (("اختبار", "قياس", "exam", "test", "quiz"), "اختبار"),
    (("ورقة عمل", "worksheet"), "ورقة عمل"),
    # ── Follow-up / attendance ───────────────────────────────────────────────────
    (("سجل", "متابعة", "followup", "follow", "كشف الحضور"), "سجل المتابعة"),
    # ── Certificates / training ─────────────────────────────────────────────────
    (("شهادة", "certificate", "دورة", "تدريب"), "الدورات والشهادات"),
    # ── School timetable / admin — must come BEFORE planning to avoid mis-match ──
    # "جدول الحصص" / "جدول مدرسي" in filename → administrative, not planning.
    (("جدول الحصص", "جدول المدرسة", "جدول مدرسي"), "ملف إداري"),
    # ── Pure administrative ──────────────────────────────────────────────────────
    (("تعميم", "قرار", "اجتماع", "circular"), "ملف إداري"),
    # ── Reports / analysis ──────────────────────────────────────────────────────
    (("تقرير", "تحليل", "نتائج", "report", "result"), "تقويم"),
    # ── Planning — curriculum distribution (توزيع) before generic "خطة" ─────────
    (("توزيع منهج", "توزيع المنهج", "توزيع الدروس", "توزيع زمني", "curriculum"), "التخطيط"),
    (("خطة", "خطط", "plan", "lesson", "تحضير"), "التخطيط"),
    # ── Classroom activity ───────────────────────────────────────────────────────
    (("نشاط", "activity"), "نشاط صفي"),
]

def _infer_pdf_category_from_name(filename: str) -> str:
    """Guess evidence category from PDF filename. Falls back to 'ملف إداري'."""
    name_lower = _normalize_arabic(filename.lower().replace("_", " ").replace("-", " "))
    for keywords, category in _PDF_CATEGORY_HINTS:
        if any(kw in name_lower for kw in keywords):
            return category
    return "ملف إداري"


def _handle_exam_choice_save(
    *,
    db: Session,
    teacher,
    background_tasks: BackgroundTasks,
) -> None:
    """Resume the evidence-save path for a teacher who chose option 1.

    Phase 2 contract: keep the existing save path semantics — we still
    persist a real ``Evidence`` row via ``create_evidence``, hash for
    deduplication and confirm with the teacher. The only difference
    vs. the GPT-driven path is that we already know the file is an
    exam / worksheet, so the category/title come from the classifier
    output instead of a second GPT round-trip.

    Errors here NEVER escape: any failure becomes a polite WhatsApp
    apology message — the teacher already saw the choice prompt and
    we owe them a definitive reply.
    """
    pending = _exam_choice.get_pending(teacher.id)
    if pending is None:
        # State expired between has_pending and get_pending — race.
        logger.info(
            "[EXAM CHOICE → SAVE] teacher_id=%d aborted — state expired",
            teacher.id,
        )
        background_tasks.add_task(
            send_whatsapp_message,
            teacher.phone,
            "انتهت مهلة الانتظار. أرسل الملف مرة أخرى من فضلك.",
            teacher_id=teacher.id,
            context="exam_choice_expired",
        )
        return

    _exam_choice.clear_pending(teacher.id)
    logger.info(
        "[EXAM CHOICE → SAVE] teacher_id=%d detected_type=%s conf=%.2f",
        teacher.id, pending.detected_type, pending.confidence,
    )

    category = _exam_rewrite_msgs.category_from_detected_type(pending.detected_type)
    title = _exam_rewrite_msgs.title_from_filename(
        pending.file_name or pending.safe_filename,
        fallback=category,
    )
    snippet = (pending.first_lines or pending.extracted_text or "").strip()
    description = (
        snippet[:300]
        if snippet
        else f"{category} تم حفظه كشاهد بطلب المعلم."
    )

    # Honour the existing media deduplication rules (same source of
    # truth as the normal PDF save path).
    if pending.media_hash and is_exact_duplicate(db, teacher.id, pending.media_hash):
        logger.info(
            "[EXAM CHOICE → SAVE] duplicate teacher_id=%d hash=%s",
            teacher.id, (pending.media_hash or "")[:12],
        )
        _dup_ev = get_evidence_by_hash(db, teacher.id, pending.media_hash)
        dup_reply = wa_integration.make_save_reply(
            ev_type="pdf",
            category=(_dup_ev.category if _dup_ev else category),
            title=(_dup_ev.title if _dup_ev else title),
            is_duplicate=True,
        )
        background_tasks.add_task(
            send_whatsapp_message,
            teacher.phone,
            dup_reply,
            teacher_id=teacher.id,
            context="exam_choice_save_duplicate",
        )
        return

    set_enrichment_teacher_context(
        name=teacher.name,
        subject=teacher.subject,
        stage=teacher.stage,
        grades=teacher.grades,
        school_name=teacher.school_name,
    )

    try:
        evidence = create_evidence(
            db=db,
            teacher_id=teacher.id,
            source_phone=teacher.phone,
            evidence_type="pdf",
            message_text=None,
            media_url=pending.media_url,
            storage_path=pending.storage_path,
            file_name=pending.safe_filename or pending.file_name,
            mime_type=pending.mime_type or "application/pdf",
            category=category,
            title=title,
            description=description,
            content_hash=pending.media_hash,
            ai_status="completed",
            ai_raw={
                "source": "exam_rewrite_choice",
                "detected_type": pending.detected_type,
                "confidence_score": pending.confidence,
                "classifier_reason": pending.classifier_reason,
            },
        )
    except Exception as save_exc:
        logger.error(
            "[EXAM CHOICE → SAVE FAILED] teacher_id=%d err=%s",
            teacher.id, save_exc, exc_info=True,
        )
        background_tasks.add_task(
            send_whatsapp_message,
            teacher.phone,
            "تعذّر حفظ الملف الآن. حاول مرة أخرى من فضلك.",
            teacher_id=teacher.id,
            context="exam_choice_save_failed",
        )
        return

    if pending.storage_path:
        background_tasks.add_task(generate_pdf_preview, pending.storage_path)

    reply = wa_integration.make_save_reply(
        ev_type="pdf",
        category=category,
        title=title,
        confidence=pending.confidence,
        ai_raw={"confidence_score": pending.confidence},
    )
    background_tasks.add_task(
        send_whatsapp_message,
        teacher.phone,
        reply,
        teacher_id=teacher.id,
        context="exam_choice_save",
    )
    logger.info(
        "[EXAM CHOICE → SAVED] teacher_id=%d evidence_id=%d category=%r title=%r",
        teacher.id, evidence.id, category, title,
    )


def _handle_exam_choice_rewrite(
    *,
    teacher,
    background_tasks: BackgroundTasks,
    storage_path: str | None,
    extracted_text: str | None,
    detected_type: str | None,
    log_context: str,
) -> None:
    """Run the Phase-3 exam analysis and reply to the teacher.

    ``storage_path`` is the on-disk PDF, ``extracted_text`` is whatever
    raw text we already had from Phase 1 (used as a fallback when the
    PDF can't be re-read — e.g. expired tmp file). The helper never
    raises: any analysis failure is reported via the failure reply
    rather than an unhandled exception.

    Phase-3 contract: the analyser produces a structured JSON object
    only. We DO NOT generate a PDF / HTML, and we DO NOT save anything
    as evidence here.
    """
    try:
        structured = _analyze_exam_pdf(
            storage_path=storage_path,
            detected_type_hint=detected_type,
            teacher_subject=teacher.subject,
            teacher_grades=teacher.grades,
            fallback_text=extracted_text,
        )
    except Exception as exc:  # pragma: no cover — defensive only
        logger.error(
            "[EXAM REWRITE ANALYSIS FAILED] teacher_id=%d ctx=%s err=%s",
            teacher.id, log_context, exc, exc_info=True,
        )
        background_tasks.add_task(
            send_whatsapp_message,
            teacher.phone,
            _exam_rewrite_msgs.build_rewrite_analysis_failure_message(),
            teacher_id=teacher.id,
            context=f"{log_context}_failure",
        )
        return

    if structured.is_usable():
        reply = _exam_rewrite_msgs.build_rewrite_analysis_success_message(
            structured,
        )
        logger.info(
            "[EXAM REWRITE ANALYSIS] teacher_id=%d ctx=%s subject=%r grade=%r "
            "type=%s questions=%d warnings=%d → success",
            teacher.id,
            log_context,
            structured.subject,
            structured.grade,
            structured.exam_type,
            structured.total_questions,
            len(structured.warnings),
        )
        background_tasks.add_task(
            send_whatsapp_message,
            teacher.phone,
            reply,
            teacher_id=teacher.id,
            context=f"{log_context}_success",
        )
        return

    logger.info(
        "[EXAM REWRITE ANALYSIS] teacher_id=%d ctx=%s — not usable, "
        "questions=%d warnings=%s",
        teacher.id,
        log_context,
        structured.total_questions,
        list(structured.warnings),
    )
    background_tasks.add_task(
        send_whatsapp_message,
        teacher.phone,
        _exam_rewrite_msgs.build_rewrite_analysis_failure_message(),
        teacher_id=teacher.id,
        context=f"{log_context}_failure",
    )


# ─── Main Webhook (POST) ─────────────────────────────────────────────────────

@router.post("/webhook/whatsapp")
async def whatsapp_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    GPT-driven conversational handler.

    Flow:
      parse → get teacher → load profile → (download image) → ask_gpt(context) → execute
      GPT decides everything. Code only executes what GPT returns.

    Logs:
      [WA INBOUND] [USER PROFILE LOADED] [GPT MODEL] [GPT DECISION]
      [PROFILE UPDATED] [EVIDENCE SAVED] [EVIDENCE SKIPPED]
      [PAYMENT BUTTON SENT]
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    parsed = _parse_meta_payload(body)
    if parsed is None:
        parsed = _parse_simple_payload(body)
    if parsed is None:
        return {"ok": True, "skipped": True}

    # Some inbound webhooks come from non-Saudi senders (status pings,
    # forwarded calls, foreign delivery receipts, etc.). The Saudi
    # normalizer raises on those — turn that into a quiet 200 OK drop
    # so the whole handler doesn't return 500 to Meta and trigger
    # retry storms. The original raw phone is logged for diagnostics.
    raw_from_phone = parsed.get("from_phone") or ""
    try:
        from_phone: str = normalize_phone(raw_from_phone)
    except (ValueError, TypeError) as exc:
        logger.warning(
            "[WA INBOUND DROPPED] reason=invalid_phone raw=%r incoming_pnid=%s detail=%s",
            raw_from_phone[:32],
            parsed.get("phone_number_id"),
            str(exc)[:120],
        )
        return {"ok": True, "dropped": "invalid_phone"}
    msg_type:   str        = parsed.get("msg_type") or "text"
    text:       str | None = parsed.get("text")
    media_id:   str | None = parsed.get("media_id")
    mime_type:  str | None = parsed.get("mime_type")
    file_name:  str | None = parsed.get("file_name")
    media_url:  str | None = parsed.get("media_url")

    # ── Tenant identity (Meta-side) ──────────────────────────────────────────
    # Exposed so a single log line is enough to verify *which* business
    # number Meta routed this message to, and so the foreign-tenant
    # guard below can drop messages destined for other services without
    # ever replying on their behalf.
    incoming_phone_number_id   = parsed.get("phone_number_id")
    incoming_display_phone     = parsed.get("display_phone_number")
    incoming_waba_id           = parsed.get("waba_id")
    incoming_contact_name      = parsed.get("contact_name")
    incoming_message_id        = parsed.get("message_id")

    logger.info(
        build_inbound_debug_line(
            phone_number_id=incoming_phone_number_id,
            display_phone_number=incoming_display_phone,
            waba_id=incoming_waba_id,
            from_phone=from_phone,
            message_text=text,
            contact_name=incoming_contact_name,
            msg_type=msg_type,
        )
    )

    # Hard guard: if Meta tells us this message was addressed to a
    # different business number, ack 200 and stop. Shawahid must never
    # reply on behalf of another tenant (e.g. Nahla / honey store).
    if is_foreign_tenant(incoming_phone_number_id):
        logger.warning(
            "[WA INBOUND DROPPED] reason=foreign_tenant service=%s "
            "incoming_phone_number_id=%s configured_suffix=%s message_id=%s",
            _TENANT_SERVICE_NAME,
            incoming_phone_number_id,
            (settings.WHATSAPP_PHONE_NUMBER_ID or "")[-4:],
            incoming_message_id,
        )
        return {"ok": True, "dropped": "foreign_tenant"}

    # Resolve temporary Meta media URL
    if media_id and not media_url:
        media_url = await get_meta_media_url(media_id)

    teacher = get_or_create_teacher(db, from_phone)
    evidence_type = detect_evidence_type(mime_type, file_name, text)

    # ── Onboarding: track first-ever interaction ──────────────────────────────
    is_new_user = not teacher.welcome_sent_at

    logger.info(
        "[WA INBOUND] teacher_id=%d type=%s has_media=%s is_new=%s text=%r",
        teacher.id, evidence_type, bool(media_id), is_new_user, (text or "")[:60],
    )

    # ── Load profile + subscription (backend-only decision) ──────────────────
    # Subscription: single DB-only source of truth. GPT never sees or decides this.
    sub_info = get_subscription_status(db, teacher.id)
    sub_active = sub_info["status"] == "active_paid"
    logger.info(
        "[SUBSCRIPTION CHECK] teacher_id=%d status=%s source=db",
        teacher.id, sub_info["status"],
    )

    # Evidence count for GPT context (so it can write natural my_files replies)
    all_evidences = get_teacher_evidences(db, teacher.id)
    evidence_count = len(all_evidences)

    teacher_context = build_teacher_context(
        name=teacher.name,
        subject=teacher.subject,
        stage=teacher.stage,
        grades=teacher.grades,
        school_name=teacher.school_name,
        principal_name=teacher.principal_name,
        region=teacher.region,
        education_admin=teacher.education_admin,
        evidence_count=evidence_count,
        is_new_user=is_new_user,
        last_profile_update=_LAST_PROFILE_UPDATES.get(teacher.id),
    )
    logger.info(
        "[USER PROFILE LOADED] teacher_id=%d name=%r evidence_count=%d",
        teacher.id, teacher.name, evidence_count,
    )

    if _is_creator_question(text) and not media_id:
        reply = "تم تطوير شواهد AI بواسطة الأستاذ تركي بن عايد الحارثي."
        background_tasks.add_task(
            send_whatsapp_message, teacher.phone, reply, teacher_id=teacher.id, context="creator_question"
        )
        return {"ok": True, "teacher_id": teacher.id, "intent": "creator_question"}

    if _is_profile_update_followup(text) and not media_id:
        reply = _format_profile_update_reply(_LAST_PROFILE_UPDATES.get(teacher.id, {}))
        background_tasks.add_task(
            send_whatsapp_message, teacher.phone, reply, teacher_id=teacher.id, context="profile_update_followup"
        )
        return {"ok": True, "teacher_id": teacher.id, "intent": "profile_update_followup"}

    # ── Pending name confirmation (audio-sourced names need explicit approval) ─
    # Whisper sometimes mishears Arabic names. We ask the teacher to confirm
    # before committing the name to the profile.
    if teacher.id in _PENDING_NAME_CONFIRMATION and not media_id:
        pending_name = _PENDING_NAME_CONFIRMATION[teacher.id]
        if _is_positive_confirmation(text):
            _PENDING_NAME_CONFIRMATION.pop(teacher.id, None)
            update_teacher(db, teacher, {"name": pending_name})
            _LAST_PROFILE_UPDATES[teacher.id] = {"name": pending_name}
            logger.info("[NAME CONFIRMED] teacher_id=%d name=%r", teacher.id, pending_name)
            reply = f"تم حفظ اسمك: {pending_name} ✅\nسيظهر في ملف الشواهد بهذا الشكل."
            background_tasks.add_task(
                send_whatsapp_message, teacher.phone, reply,
                teacher_id=teacher.id, context="name_confirmed"
            )
            return {"ok": True, "teacher_id": teacher.id, "intent": "name_confirmed"}
        elif _is_negative_confirmation(text):
            _PENDING_NAME_CONFIRMATION.pop(teacher.id, None)
            logger.info("[NAME REJECTED] teacher_id=%d — teacher said no, asking for text input", teacher.id)
            reply = (
                "لا بأس! 😊\n"
                "اكتب اسمك الكامل نصًا حتى أحفظه بدقة\n"
                "(التفريغ الصوتي أحيانًا يخطئ في الأسماء)"
            )
            background_tasks.add_task(
                send_whatsapp_message, teacher.phone, reply,
                teacher_id=teacher.id, context="name_rejected"
            )
            return {"ok": True, "teacher_id": teacher.id, "intent": "name_rejected"}
        # If neither yes nor no, fall through to normal GPT processing
        # (teacher might be sending new evidence while confirmation is pending)

    # ── Pending exam-rewrite choice (Phase 2) ─────────────────────────────────
    # When the previous PDF was identified as an exam / worksheet we asked the
    # teacher "1️⃣ save as evidence  /  2️⃣ rewrite on school template". A text
    # reply (no media) is the answer to that question, so we must short-circuit
    # before the GPT router sees it — GPT would otherwise classify "1" as
    # gibberish and lose context.
    if (
        text
        and not media_id
        and _exam_choice.has_pending(teacher.id)
    ):
        _choice = _exam_choice.parse_exam_choice(text)
        if _choice == "save":
            _handle_exam_choice_save(
                db=db,
                teacher=teacher,
                background_tasks=background_tasks,
            )
            return {
                "ok": True,
                "teacher_id": teacher.id,
                "intent": "exam_choice_save",
            }
        if _choice == "rewrite":
            pending = _exam_choice.get_pending(teacher.id)
            _exam_choice.clear_pending(teacher.id)
            logger.info(
                "[EXAM CHOICE → REWRITE] teacher_id=%d — running analysis",
                teacher.id,
            )
            _handle_exam_choice_rewrite(
                teacher=teacher,
                background_tasks=background_tasks,
                storage_path=(pending.storage_path if pending else None),
                extracted_text=(pending.extracted_text if pending else None),
                detected_type=(pending.detected_type if pending else None),
                log_context="exam_choice_rewrite",
            )
            return {
                "ok": True,
                "teacher_id": teacher.id,
                "intent": "exam_choice_rewrite_analysis",
            }
        # Unclear reply — send a short clarification but keep the pending
        # state so the teacher's next message can still answer the prompt.
        logger.info(
            "[EXAM CHOICE → UNCLEAR] teacher_id=%d text=%r",
            teacher.id, (text or "")[:60],
        )
        background_tasks.add_task(
            send_whatsapp_message,
            teacher.phone,
            _exam_rewrite_msgs.build_choice_clarification_message(),
            teacher_id=teacher.id,
            context="exam_choice_unclear",
        )
        return {
            "ok": True,
            "teacher_id": teacher.id,
            "intent": "exam_choice_unclear",
        }

    # ── Pre-export button replies: مراجعة الملف / تصدير الآن ─────────────────
    _normalized_text = _normalize_arabic(text or "")

    # ── Deterministic button-payload short-circuit (MUST run before the
    # GPT router). When the teacher taps an interactive WhatsApp button,
    # the parser stores ``interactive.button_reply.id`` in ``text`` (e.g.
    # "export_now", "review_file", "export_full"). These IDs must NEVER
    # be classified by GPT — otherwise GPT sees "export_now" and routes
    # the user back to the export-portfolio handler, which re-sends the
    # same مراجعة/تصدير card → infinite loop.
    #
    # We handle the two pre-export buttons here directly. The export-mode
    # buttons (export_full / export_smart / export_short) are caught by
    # ``_parse_export_mode`` further down — that path is already loop-safe.
    _BUTTON_REVIEW_FILE = {"review_file", "🔍 مراجعه الملف", "🔍 مراجعة الملف"}
    _BUTTON_EXPORT_NOW = {"export_now", "📤 تصدير الان", "📤 تصدير الآن"}

    if text and not media_id and _normalized_text in _BUTTON_REVIEW_FILE:
        _AWAITING_EXPORT_CHOICE.discard(teacher.id)
        logger.info(
            "[EXPORT DECISION_SELECTED] teacher_id=%d action=review_file",
            teacher.id,
        )
        from app.services.teachers import get_or_create_review_token
        _rt = get_or_create_review_token(db, teacher)
        _rv = f"{settings.effective_base_url}/review/{_rt}"
        _all_evidences = get_teacher_evidences(db, teacher.id)
        _review_reply = wa_integration.make_review_link_reply(
            _all_evidences,
            teacher_id=teacher.id,
            teacher_name=teacher.name,
            base_url=settings.effective_base_url,
            review_url=_rv,
        )
        background_tasks.add_task(
            send_whatsapp_message, teacher.phone, _review_reply,
            teacher_id=teacher.id, context="review_link_sent",
        )
        db.commit()
        return {
            "ok": True, "teacher_id": teacher.id,
            "intent": "review_requested", "via": "button",
        }

    if text and not media_id and _normalized_text in _BUTTON_EXPORT_NOW:
        _AWAITING_EXPORT_CHOICE.discard(teacher.id)
        logger.info(
            "[EXPORT DECISION_SELECTED] teacher_id=%d action=export_now",
            teacher.id,
        )
        if not sub_active:
            try:
                payment_url = await _create_moyasar_link(
                    db, teacher.id, teacher.name or "", teacher.phone or "",
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("Moyasar invoice creation failed: %s", exc)
                payment_url = get_payment_link(teacher.id)
            background_tasks.add_task(
                send_whatsapp_message, teacher.phone,
                build_payment_link_message(payment_url, teacher.name or ""),
                teacher_id=teacher.id, context="payment_link",
            )
            db.commit()
            return {
                "ok": False, "teacher_id": teacher.id,
                "reason": "subscription_required",
            }
        _PENDING_EXPORT_REQUESTS.add(teacher.id)
        logger.info(
            "[EXPORT MODE_PROMPTED] teacher_id=%d via=button", teacher.id,
        )
        background_tasks.add_task(
            send_export_options_buttons,
            teacher.phone, teacher_id=teacher.id,
        )
        db.commit()
        return {
            "ok": True, "teacher_id": teacher.id,
            "intent": "export_mode_offered",
            "awaiting_export_mode": True,
            "via": "button",
        }

    # ── Stage-3 short-circuit: export-mode buttons (كامل / ذكي / مختصر) ─────
    # When the teacher taps one of the three mode reply-buttons,
    # WhatsApp delivers ``button_reply.id`` ∈ {export_full, export_smart,
    # export_short}. Without this short-circuit those IDs would reach
    # the GPT router, which sees the substring "export" and routes
    # them as another export-portfolio request — re-sending the
    # مراجعة/تصدير card and stranding the teacher one step before the
    # actual generation.
    #
    # Rules:
    #   • Button IDs (``export_full`` / ``export_smart`` / ``export_short``)
    #     ALWAYS short-circuit — those IDs are unambiguous.
    #   • Arabic / English plain text ("كامل" / "ذكي" / "smart" / "1") only
    #     short-circuits when the teacher is currently in
    #     ``_PENDING_EXPORT_REQUESTS`` (i.e. we just asked them to pick
    #     a mode). This keeps random "كامل" messages out of the export
    #     pipeline.
    _BUTTON_MODE_IDS = {"export_full", "export_smart", "export_short"}
    if text and not media_id:
        _norm = _normalized_text
        _is_mode_button = _norm in _BUTTON_MODE_IDS
        _selected_mode = _parse_export_mode(text)
        if _selected_mode and (
            _is_mode_button or teacher.id in _PENDING_EXPORT_REQUESTS
        ):
            # Belt-and-suspenders: if the teacher is somehow STILL in the
            # awaiting-choice set when they pick a mode (shouldn't happen
            # — the button short-circuit above clears it — but log it so
            # we'd notice), force-clear and continue with the export.
            if teacher.id in _AWAITING_EXPORT_CHOICE:
                logger.info(
                    "[EXPORT MODE_LOOP_PREVENTED] teacher_id=%d "
                    "mode=%s msg=%r forcing_clear_awaiting=True",
                    teacher.id, _selected_mode, (text or "")[:40],
                )
                _AWAITING_EXPORT_CHOICE.discard(teacher.id)

            _resp = await _handle_export_mode_selection(
                teacher=teacher,
                db=db,
                background_tasks=background_tasks,
                sub_active=sub_active,
                sub_info=sub_info,
                selected_export_mode=_selected_mode,
                text=text,
                via="pre_router_button" if _is_mode_button else "pre_router_text",
            )
            db.commit()
            return _resp

    # ── High-priority deterministic export-intent short-circuit ──────────────
    # GPT, given a single bare word like "صدر", will reasonably ask
    # "ما الذي تقصده؟" — but in a teacher-portfolio bot that word
    # *always* means "export my portfolio". We must therefore detect
    # export intent BEFORE the GPT router runs, otherwise the teacher
    # gets stuck on a clarification loop.
    #
    # Two paths:
    #   1. Bare single-token export commands ("صدر", "تصدير", …) —
    #      checked against ``_EXPORT_BARE_COMMANDS`` after Arabic
    #      normalisation.
    #   2. Multi-word phrases like "أرسل الملف" / "جهز الملف" — caught
    #      by ``is_export_command`` (which already handles diacritics,
    #      hamza variants, and shadda).
    if text and not media_id:
        _is_bare_export = _normalized_text in _EXPORT_BARE_COMMANDS
        if _is_bare_export or is_export_command(text):
            logger.info(
                "[EXPORT INTENT DETECTED] teacher_id=%d msg=%r "
                "via=%s pre_router=True",
                teacher.id, (text or "")[:40],
                "bare_command" if _is_bare_export else "phrase",
            )
            _resp = await _run_pre_export_choice_flow(
                teacher=teacher, db=db,
                background_tasks=background_tasks,
                sub_active=sub_active,
                text=text,
                via="pre_router_bare" if _is_bare_export else "pre_router_phrase",
            )
            db.commit()
            return _resp

    # ── Phase-13: GPT-First Router (text-only path) ──────────────────────────
    # GPT decides ONE action for plain-text messages BEFORE any save/dispatch.
    # The router handles chat_reply / ask_clarification / delete_or_edit /
    # update_profile / create_exam / export_portfolio / review_portfolio
    # entirely on its own. For save_evidence and unknown it returns None and
    # lets the legacy ask_gpt path run.
    if text and not media_id:
        _router_decision, _router_response = await _route_via_gpt_router(
            message=text,
            teacher=teacher,
            db=db,
            background_tasks=background_tasks,
            has_media=False,
            media_type=None,
            has_transcript=False,
            sub_active=sub_active,
        )
        if _router_response is not None:
            db.commit()
            return _router_response

    # Phase-6: semantic intent detection — runs before GPT to short-circuit
    # obvious commands without paying an OpenAI round-trip.
    _wa_intent = wa_integration.resolve_text_intent(text) if (text and not media_id) else None

    # ── Phase-12: exam flow routing ───────────────────────────────────────────
    # When the teacher says "أريد اختبار رياضيات" (or similar) we run the
    # exam conversation orchestrator instead of the evidence pipeline.
    # The webhook sends the orchestrator's reply + (optionally) the PDF.
    from app.services.intents import (
        INTENT_CREATE_EXAM,
        INTENT_EXAM_CONFIRM,
        INTENT_EXAM_EXPORT,
        INTENT_EXAM_REGENERATE,
    )
    from app.exam_engine.exam_flow import STAGE_READY, STAGE_MISSING_INFO

    _exam_intent_names = (
        INTENT_CREATE_EXAM,
        INTENT_EXAM_CONFIRM,
        INTENT_EXAM_EXPORT,
        INTENT_EXAM_REGENERATE,
    )
    _is_exam_intent = bool(
        _wa_intent
        and _wa_intent.intent in _exam_intent_names
    )
    # When the exam state is mid-conversation (teacher already started a
    # request) we keep routing follow-up text through the exam flow even
    # if the intent detector didn't fire on this specific message.
    _is_exam_followup = False
    if not _is_exam_intent and not media_id and text:
        try:
            from app.conversation_engine.exam_state import get_exam_state
            _ex_state = get_exam_state(teacher.id)
            _is_exam_followup = bool(_ex_state.pending_fields)
        except Exception:  # noqa: BLE001
            _is_exam_followup = False

    if (_is_exam_intent or _is_exam_followup) and not media_id:
        logger.info(
            "[EXAM FLOW] teacher_id=%d via=%s text=%r",
            teacher.id,
            "intent" if _is_exam_intent else "followup",
            (text or "")[:60],
        )
        _grades_tuple: tuple[str, ...] = ()
        if teacher.grades:
            if isinstance(teacher.grades, (list, tuple)):
                _grades_tuple = tuple(str(g) for g in teacher.grades if g)
            else:
                _grades_tuple = (str(teacher.grades),)

        _exam_result = wa_integration.make_exam_flow_result(
            teacher_id=teacher.id,
            text=text,
            teacher_name=teacher.name,
            school_name=teacher.school_name,
            education_admin=teacher.education_admin,
            region=teacher.region,
            teacher_subject=teacher.subject,
            teacher_stage=teacher.stage,
            teacher_grades=_grades_tuple,
            render_pdf=True,
        )

        if _exam_result is not None:
            # Optional progress messages first (fetch / source-found).
            for _pm in (_exam_result.progress_messages or []):
                background_tasks.add_task(
                    send_whatsapp_message, teacher.phone, _pm,
                    teacher_id=teacher.id, context="exam_progress",
                )
            background_tasks.add_task(
                send_whatsapp_message, teacher.phone, _exam_result.reply_text,
                teacher_id=teacher.id, context=f"exam_{_exam_result.stage}",
            )
            # Phase-13: schedule the download CTA button (with text-link
            # fallback) and persist the URL on conversation state so
            # follow-up "أين الرابط؟" questions resolve instantly.
            if _exam_result.is_ready and _exam_result.exam is not None:
                from app.api.exam_downloads import build_exam_download_url
                from app.conversation_engine.exam_state import (
                    update_last_exam_download_url,
                )
                _exam_id = _exam_result.exam.exam_id
                _download_url = build_exam_download_url(
                    teacher_id=teacher.id, exam_id=_exam_id,
                )
                update_last_exam_download_url(
                    teacher.id, download_url=_download_url,
                )
                background_tasks.add_task(
                    _send_exam_download_button,
                    teacher_phone=teacher.phone,
                    teacher_id=teacher.id,
                    exam_id=_exam_id,
                    download_url=_download_url,
                    subject=_exam_result.exam.profile.subject,
                    grade=_exam_result.exam.profile.grade,
                    exam_type=_exam_result.exam.profile.exam_type,
                    exam=_exam_result.exam,
                )
            return {
                "ok": True,
                "teacher_id": teacher.id,
                "intent": "exam_flow",
                "exam_stage": _exam_result.stage,
            }

    # ── Category hint: store for the next evidence save ───────────────────────
    if (
        _wa_intent
        and _wa_intent.intent == "category"
        and (_wa_intent.payload or {}).get("category")
        and not media_id
    ):
        _hint_cat = _wa_intent.payload["category"]  # type: ignore[index]
        _PENDING_CATEGORY_HINT[teacher.id] = _hint_cat
        logger.info(
            "[CATEGORY HINT] teacher_id=%d hint=%r (stored for next save)",
            teacher.id, _hint_cat,
        )
        background_tasks.add_task(
            send_whatsapp_message, teacher.phone,
            f"سأحفظ الشاهد القادم في محور: {_hint_cat} ✅",
            teacher_id=teacher.id, context="category_hint_ack",
        )
        return {"ok": True, "teacher_id": teacher.id, "intent": "category_hint"}

    # ── Review request: button ID *or* natural-language intent ────────────────
    _is_review_intent = (
        _normalized_text in ("review_file", "مراجعة الملف", "مراجعه الملف")
        or (_wa_intent is not None and _wa_intent.intent == "review")
    )
    if _is_review_intent and not media_id:
        _AWAITING_EXPORT_CHOICE.discard(teacher.id)
        _via = "button" if _normalized_text in ("review_file", "مراجعة الملف", "مراجعه الملف") else "intent"
        logger.info("[REVIEW REQUESTED] teacher_id=%d via=%s", teacher.id, _via)
        from app.services.teachers import get_or_create_review_token
        _rt = get_or_create_review_token(db, teacher)
        _rv = f"{settings.effective_base_url}/review/{_rt}"
        _all_evidences = get_teacher_evidences(db, teacher.id)
        _review_reply = wa_integration.make_review_link_reply(
            _all_evidences,
            teacher_id=teacher.id,
            teacher_name=teacher.name,
            base_url=settings.effective_base_url,
            review_url=_rv,
        )
        background_tasks.add_task(
            send_whatsapp_message, teacher.phone, _review_reply,
            teacher_id=teacher.id, context="review_link_sent",
        )
        return {"ok": True, "teacher_id": teacher.id, "intent": "review_requested"}

    # "export_now" = WhatsApp button id (always bypass to mode selection).
    # Typed Arabic variants only bypass if already in the awaiting-choice state.
    _is_export_now = (
        _normalized_text == "export_now"
        or (_normalized_text in ("تصدير الان", "صدر الان") and teacher.id in _AWAITING_EXPORT_CHOICE)
    )
    if _is_export_now and not media_id:
        _AWAITING_EXPORT_CHOICE.discard(teacher.id)
        logger.info(
            "[EXPORT DECISION_SELECTED] teacher_id=%d "
            "action=export_now via=text",
            teacher.id,
        )
        if not sub_active:
            try:
                payment_url = await _create_moyasar_link(
                    db, teacher.id, teacher.name or "", teacher.phone or ""
                )
            except Exception as exc:
                logger.error("Moyasar invoice creation failed: %s", exc)
                payment_url = get_payment_link(teacher.id)
            background_tasks.add_task(
                send_whatsapp_message,
                teacher.phone,
                build_payment_link_message(payment_url, teacher.name or ""),
                teacher_id=teacher.id,
                context="payment_link",
            )
            return {"ok": False, "teacher_id": teacher.id, "reason": "subscription_required"}
        _PENDING_EXPORT_REQUESTS.add(teacher.id)
        logger.info(
            "[EXPORT MODE_PROMPTED] teacher_id=%d via=text", teacher.id,
        )
        background_tasks.add_task(
            send_export_options_buttons,
            teacher.phone,
            teacher_id=teacher.id,
        )
        return {"ok": True, "teacher_id": teacher.id, "intent": "export_mode_offered", "awaiting_export_mode": True}

    # ── Direct export shortcut: show 2-button choice card ────────────────────
    # Catches any Arabic export variant: صدر / اصدر / ارسل الملف / خلاص صدر …
    # Now a thin backstop — the high-priority short-circuit above already
    # caught most cases, but we keep this so legacy flows that bypass
    # the router (e.g. some downstream rewrites) still work.
    if is_export_command(text) and not media_id:
        return await _run_pre_export_choice_flow(
            teacher=teacher, db=db,
            background_tasks=background_tasks,
            sub_active=sub_active,
            text=text, via="legacy_export_phrase",
        )

    # ── Export mode selection shortcut ────────────────────────────────────────
    # Backstop for cases the pre-router short-circuit didn't catch
    # (e.g. teacher types "ذكي" without a button when not in
    # _PENDING_EXPORT_REQUESTS and the GPT router routes it here).
    # The helper handles all paywall + start logic uniformly.
    selected_export_mode = _parse_export_mode(text)
    if selected_export_mode and not media_id:
        return await _handle_export_mode_selection(
            teacher=teacher,
            db=db,
            background_tasks=background_tasks,
            sub_active=sub_active,
            sub_info=sub_info,
            selected_export_mode=selected_export_mode,
            text=text,
            via="legacy_mode_selection",
        )

    # ── PDF: send immediate acknowledgment before any processing ─────────────
    # PDFs are critical (خطط / سجلات / اختبارات). Never stay silent.
    _is_pdf_msg = (
        mime_type == "application/pdf"
        or (file_name or "").lower().endswith(".pdf")
        or evidence_type == "pdf"
    )
    if _is_pdf_msg and media_id:
        logger.info(
            "[PDF RECEIVED] teacher_id=%d file=%s mime=%s",
            teacher.id, file_name, mime_type,
        )
        # Receipt confirmation — explicit "saved" message is sent ONLY after
        # verify_evidence_in_export confirms the row is persisted.
        # Phase-6: uses build_file_received_message for consistent tone.
        background_tasks.add_task(
            send_whatsapp_message,
            teacher.phone,
            wa_integration.make_file_received_reply("pdf"),
            teacher_id=teacher.id,
            context="pdf_received",
        )
    elif media_id and evidence_type and evidence_type not in ("text", "url"):
        # Phase-6: universal immediate ack for images, video, audio, documents.
        # Keeps the teacher informed the file arrived before GPT runs.
        background_tasks.add_task(
            send_whatsapp_message,
            teacher.phone,
            wa_integration.make_file_received_reply(evidence_type),
            teacher_id=teacher.id,
            context="media_received",
        )

    # ── Download media (WhatsApp URLs require auth — must download first) ─────
    storage_path:   str | None = None
    safe_filename:  str | None = None
    media_hash:     str | None = None

    if media_url:
        try:
            storage_path, safe_filename, media_hash = await download_and_save(
                teacher_id=teacher.id,
                media_url=media_url,
                original_filename=file_name,
                mime_type=mime_type,
                auth_token=settings.WHATSAPP_ACCESS_TOKEN or None,
            )
            if _is_pdf_msg and storage_path:
                logger.info(
                    "[PDF DOWNLOADED] teacher_id=%d path=%s size=%.1fKB",
                    teacher.id, storage_path,
                    Path(storage_path).stat().st_size / 1024,
                )
        except Exception as exc:
            logger.error("Media download failed for teacher %d: %s", teacher.id, exc)
            if _is_pdf_msg:
                logger.error("[PDF FAILED] teacher_id=%d — download failed: %s", teacher.id, exc)

    # ── Media deduplication: exact byte-hash check ────────────────────────────
    if media_hash and is_exact_duplicate(db, teacher.id, media_hash):
        logger.info(
            "[DUPLICATE SKIPPED] media already exists teacher_id=%d hash=%s",
            teacher.id, media_hash[:12],
        )
        _dup_ev = get_evidence_by_hash(db, teacher.id, media_hash)
        _dup_msg = wa_integration.make_save_reply(
            ev_type=(_dup_ev.evidence_type if _dup_ev else evidence_type or "document"),
            category=(_dup_ev.category if _dup_ev else ""),
            title=(_dup_ev.title if _dup_ev else None),
            is_duplicate=True,
        )
        background_tasks.add_task(
            send_whatsapp_message,
            teacher.phone,
            _dup_msg,
            teacher_id=teacher.id,
            context="duplicate_media",
        )
        return {"ok": True, "teacher_id": teacher.id, "duplicate": True, "reason": "media_hash"}

    # ── Audio / Video transcription ───────────────────────────────────────────
    transcript:          str | None = None
    thumbnail_path:      str | None = None
    transcription_failed: bool      = False
    is_video_msg:         bool      = msg_type == "video"

    if msg_type in ("audio", "voice") and storage_path:
        logger.info(
            "[AUDIO RECEIVED] teacher_id=%d mime=%s file=%s",
            teacher.id, mime_type, safe_filename,
        )
        transcript = await transcribe_svc.transcribe_audio(
            Path(storage_path)
        )
        if transcript:
            logger.info(
                "[TRANSCRIBE SUCCESS] teacher_id=%d chars=%d",
                teacher.id, len(transcript),
            )
        else:
            transcription_failed = True
            logger.warning(
                "[TRANSCRIBE FAILED] teacher_id=%d — audio not readable",
                teacher.id,
            )

    elif msg_type == "video" and storage_path:
        logger.info(
            "[VIDEO RECEIVED] teacher_id=%d mime=%s file=%s",
            teacher.id, mime_type, safe_filename,
        )
        # Extract audio track for transcription
        audio_path = transcribe_svc.extract_audio_from_video(Path(storage_path))
        if audio_path:
            transcript = await transcribe_svc.transcribe_audio(audio_path)
            if transcript:
                logger.info(
                    "[TRANSCRIBE SUCCESS] teacher_id=%d chars=%d (from video)",
                    teacher.id, len(transcript),
                )

        # Extract thumbnail for GPT Vision (even if transcription failed)
        thumb = transcribe_svc.extract_video_thumbnail(Path(storage_path))
        if thumb:
            thumbnail_path = str(thumb)

        if not transcript:
            # Still proceed with thumbnail-only analysis if we have one
            if not thumbnail_path:
                transcription_failed = True
            logger.warning(
                "[TRANSCRIBE FAILED] teacher_id=%d — video audio not readable%s",
                teacher.id,
                " (thumbnail available)" if thumbnail_path else " (no thumbnail either)",
            )

    # ── PDF: smart multi-page extraction + deep GPT document analysis ─────────
    # Strategy:
    #   1. extract_pdf_smart() → reads all pages, detects document signals
    #   2. analyze_pdf_document() → GPT classifies from actual content
    #   3. Results pre-populate title/category/description so main GPT call
    #      receives rich, structured context — not just a raw filename hint.
    _pdf_text: str | None = None
    _pdf_preanalysis: dict | None = None  # result from analyze_pdf_document()

    if _is_pdf_msg and storage_path:
        logger.info("[PDF ANALYSIS START] teacher_id=%d file=%s", teacher.id, safe_filename)

        _pdf_extract = extract_pdf_smart(storage_path, max_chars=3500)

        if _pdf_extract and not _pdf_extract.is_empty:
            _pdf_text = _pdf_extract.full_text
            logger.info(
                "[PDF TEXT EXTRACTED] teacher_id=%d chars=%d pages=%d/%d signals=%s",
                teacher.id, len(_pdf_text),
                _pdf_extract.pages_with_text, _pdf_extract.page_count,
                _pdf_extract.detected_keywords or "none",
            )

            # ── Phase 2: PDF kind classifier + stash-and-ask gate ────────
            # When the classifier says this PDF is an exam / worksheet
            # with high confidence we MUST NOT save it as evidence
            # automatically. Instead we ask the teacher to choose:
            #   1️⃣ save as evidence
            #   2️⃣ rewrite on the school template
            # When the teacher's caption already explicitly asks to
            # rewrite, we skip the question and go straight to the
            # rewrite-pending placeholder (the rewrite engine itself
            # is being built in later phases).
            try:
                _pdf_kind_result = classify_pdf_kind(
                    extracted_text=_pdf_text,
                    filename=file_name or safe_filename,
                    first_lines=_pdf_extract.first_lines,
                    has_questions=_pdf_extract.has_questions,
                    has_grades_table=_pdf_extract.has_grades_table,
                    has_objectives=_pdf_extract.has_objectives,
                    detected_keywords=_pdf_extract.detected_keywords,
                )
                logger.info(
                    "[PDF KIND CLASSIFIER] teacher_id=%d kind=%s conf=%.2f "
                    "detected_type=%s reason=%s",
                    teacher.id,
                    _pdf_kind_result.get("pdf_kind"),
                    float(_pdf_kind_result.get("confidence", 0.0)),
                    _pdf_kind_result.get("detected_type"),
                    _pdf_kind_result.get("reason"),
                )
            except Exception as _cls_exc:
                logger.warning(
                    "[PDF KIND CLASSIFIER] teacher_id=%d skipped — error: %s",
                    teacher.id, _cls_exc,
                )
                _pdf_kind_result = None

            if _pdf_kind_result:
                _kind = _pdf_kind_result.get("pdf_kind")
                _conf = float(_pdf_kind_result.get("confidence", 0.0))
                _detected = _pdf_kind_result.get("detected_type")
                _explicit_rewrite = (
                    _exam_choice.parse_exam_choice(text or "") == "rewrite"
                )

                # 1) Strong exam/worksheet signal → ask the teacher.
                if (
                    _kind == "exam_or_worksheet"
                    and _conf >= 0.70
                    and not _explicit_rewrite
                ):
                    _exam_choice.set_pending(
                        teacher.id,
                        storage_path=storage_path,
                        file_name=file_name,
                        safe_filename=safe_filename,
                        mime_type=mime_type,
                        media_url=media_url,
                        media_id=media_id,
                        media_hash=media_hash,
                        detected_type=_detected,
                        confidence=_conf,
                        classifier_reason=_pdf_kind_result.get("reason", ""),
                        extracted_text=_pdf_text,
                        first_lines=_pdf_extract.first_lines,
                    )
                    logger.info(
                        "[EXAM CHOICE STASHED] teacher_id=%d detected_type=%s conf=%.2f",
                        teacher.id, _detected, _conf,
                    )
                    background_tasks.add_task(
                        send_whatsapp_message,
                        teacher.phone,
                        _exam_rewrite_msgs.build_choice_prompt(),
                        teacher_id=teacher.id,
                        context="exam_rewrite_choice_prompt",
                    )
                    return {
                        "ok": True,
                        "teacher_id": teacher.id,
                        "intent": "exam_rewrite_choice_pending",
                    }

                # 2) Teacher already asked explicitly to rewrite this
                # file in their caption → no question, go straight to
                # the rewrite analysis path (Phase 3).
                if _explicit_rewrite:
                    logger.info(
                        "[EXAM REWRITE DIRECT] teacher_id=%d kind=%s conf=%.2f "
                        "— explicit rewrite signal in caption",
                        teacher.id, _kind, _conf,
                    )
                    _handle_exam_choice_rewrite(
                        teacher=teacher,
                        background_tasks=background_tasks,
                        storage_path=storage_path,
                        extracted_text=_pdf_text,
                        detected_type=_detected,
                        log_context="exam_rewrite_direct",
                    )
                    return {
                        "ok": True,
                        "teacher_id": teacher.id,
                        "intent": "exam_rewrite_direct_analysis",
                    }
                # 3) evidence / unknown without rewrite signal — fall
                # through to the normal save pipeline unchanged.

            # ── Deep document analysis via GPT ───────────────────────────────
            # Run synchronously (we're already inside a background-safe path).
            # This gives us: document_type, category, title, description, keywords.
            from app.services.gpt_brain import analyze_pdf_document as _analyze_pdf

            _pdf_preanalysis = _analyze_pdf(
                extracted_text=_pdf_text,
                first_lines=_pdf_extract.first_lines,
                filename=file_name or safe_filename,
                page_count=_pdf_extract.page_count,
                pages_with_text=_pdf_extract.pages_with_text,
                has_tables=_pdf_extract.has_tables,
                has_questions=_pdf_extract.has_questions,
                has_objectives=_pdf_extract.has_objectives,
                has_grades_table=_pdf_extract.has_grades_table,
                has_ministry_header=_pdf_extract.has_ministry_header,
                detected_keywords=_pdf_extract.detected_keywords,
                teacher_name=teacher.name,
                subject=teacher.subject,
                stage=teacher.stage,
                grades=teacher.grades,
            )

            if _pdf_preanalysis:
                # Build a rich transcript for the main GPT call
                _doc_type   = _pdf_preanalysis.get("document_type", "")
                _doc_cat    = _pdf_preanalysis.get("category", "")
                _doc_title  = _pdf_preanalysis.get("title", "")
                _doc_desc   = _pdf_preanalysis.get("description", "")
                _doc_kws    = ", ".join(_pdf_preanalysis.get("keywords") or [])
                transcript = (
                    f"اسم الملف: {file_name or safe_filename}\n"
                    f"نوع الوثيقة (تحليل ذكي): {_doc_type}\n"
                    f"التصنيف المقترح: {_doc_cat}\n"
                    f"العنوان المقترح: {_doc_title}\n"
                    f"الوصف: {_doc_desc}\n"
                    f"كلمات مفتاحية: {_doc_kws}\n\n"
                    f"محتوى الملف (مستخرج):\n{_pdf_text[:1500]}"
                )
                logger.info(
                    "[PDF ANALYSIS DONE] teacher_id=%d type=%r cat=%r title=%r conf=%.2f",
                    teacher.id,
                    _doc_type,
                    _doc_cat,
                    _doc_title,
                    float(_pdf_preanalysis.get("confidence", 0)),
                )
            else:
                # GPT analysis failed — fall back to raw text for main GPT
                transcript = (
                    f"اسم الملف: {file_name or safe_filename}\n\n"
                    f"محتوى الملف (مستخرج):\n{_pdf_text[:2000]}"
                )
                logger.info("[PDF ANALYSIS DONE] teacher_id=%d — no pre-analysis, using raw text", teacher.id)

        else:
            # Scanned PDF or empty — only filename hint available
            transcript = f"ملف PDF: {file_name or safe_filename or 'غير محدد'}"
            logger.info(
                "[PDF NO TEXT] teacher_id=%d — scanned/empty PDF, using filename hint",
                teacher.id,
            )

            # ── Phase 2: PDF kind classifier (filename-only path) ────────
            # Scanned/empty PDFs only carry filename signal. Confidence
            # is therefore low almost always, so the choice question
            # will rarely fire — but the explicit-rewrite-in-caption
            # shortcut still applies.
            try:
                _pdf_kind_result = classify_pdf_kind(
                    extracted_text=None,
                    filename=file_name or safe_filename,
                    first_lines=None,
                    has_questions=False,
                    has_grades_table=False,
                    has_objectives=False,
                    detected_keywords=None,
                )
                logger.info(
                    "[PDF KIND CLASSIFIER] teacher_id=%d kind=%s conf=%.2f "
                    "detected_type=%s reason=%s (filename-only)",
                    teacher.id,
                    _pdf_kind_result.get("pdf_kind"),
                    float(_pdf_kind_result.get("confidence", 0.0)),
                    _pdf_kind_result.get("detected_type"),
                    _pdf_kind_result.get("reason"),
                )
            except Exception as _cls_exc:
                logger.warning(
                    "[PDF KIND CLASSIFIER] teacher_id=%d skipped — error: %s",
                    teacher.id, _cls_exc,
                )
                _pdf_kind_result = None

            if _pdf_kind_result:
                _kind = _pdf_kind_result.get("pdf_kind")
                _conf = float(_pdf_kind_result.get("confidence", 0.0))
                _detected = _pdf_kind_result.get("detected_type")
                _explicit_rewrite = (
                    _exam_choice.parse_exam_choice(text or "") == "rewrite"
                )

                if (
                    _kind == "exam_or_worksheet"
                    and _conf >= 0.70
                    and not _explicit_rewrite
                ):
                    _exam_choice.set_pending(
                        teacher.id,
                        storage_path=storage_path,
                        file_name=file_name,
                        safe_filename=safe_filename,
                        mime_type=mime_type,
                        media_url=media_url,
                        media_id=media_id,
                        media_hash=media_hash,
                        detected_type=_detected,
                        confidence=_conf,
                        classifier_reason=_pdf_kind_result.get("reason", ""),
                        extracted_text=None,
                        first_lines=None,
                    )
                    logger.info(
                        "[EXAM CHOICE STASHED] teacher_id=%d detected_type=%s conf=%.2f "
                        "(filename-only)",
                        teacher.id, _detected, _conf,
                    )
                    background_tasks.add_task(
                        send_whatsapp_message,
                        teacher.phone,
                        _exam_rewrite_msgs.build_choice_prompt(),
                        teacher_id=teacher.id,
                        context="exam_rewrite_choice_prompt",
                    )
                    return {
                        "ok": True,
                        "teacher_id": teacher.id,
                        "intent": "exam_rewrite_choice_pending",
                    }

                if _explicit_rewrite:
                    logger.info(
                        "[EXAM REWRITE DIRECT] teacher_id=%d kind=%s conf=%.2f "
                        "— explicit rewrite signal in caption (filename-only)",
                        teacher.id, _kind, _conf,
                    )
                    _handle_exam_choice_rewrite(
                        teacher=teacher,
                        background_tasks=background_tasks,
                        storage_path=storage_path,
                        extracted_text=None,
                        detected_type=_detected,
                        log_context="exam_rewrite_direct",
                    )
                    return {
                        "ok": True,
                        "teacher_id": teacher.id,
                        "intent": "exam_rewrite_direct_analysis",
                    }

    # ── Transcription failure: notify user before calling GPT ────────────────
    # We do NOT call GPT when we have no content to classify (no text, no transcript,
    # no thumbnail). Sending a meaningful failure message is more honest.
    if transcription_failed and not text and not thumbnail_path:
        failure_msg = (
            "وصلني الملف، لكن لم أتمكن من قراءة الصوت بوضوح 🙏\n"
            "يمكنك إرسال وصف مختصر لأحفظه معك كشاهد."
        )
        background_tasks.add_task(
            send_whatsapp_message, teacher.phone, failure_msg, teacher_id=teacher.id
        )
        logger.info("[TRANSCRIBE FAILED] sent fallback reply to teacher_id=%d", teacher.id)
        return {"ok": True, "teacher_id": teacher.id, "intent": "transcription_failed"}

    # ── Phase-13: GPT-First Router (voice transcript path) ───────────────────
    # When a teacher sends a voice note that asks for an exam, updates their
    # profile, or is just chat — we must NOT silently save the audio as an
    # evidence row. Route the transcript through the same router; if it
    # decides a non-save action, return early and skip ask_gpt entirely.
    if msg_type in ("audio", "voice") and transcript:
        _v_router_decision, _v_router_response = await _route_via_gpt_router(
            message=transcript,
            teacher=teacher,
            db=db,
            background_tasks=background_tasks,
            has_media=True,
            media_type="audio",
            has_transcript=True,
            sub_active=sub_active,
        )
        if _v_router_response is not None:
            db.commit()
            return _v_router_response

    # ── Detect URLs in text (YouTube, websites, Google Drive, etc.) ──────────
    urls_in_text: list[str] = extract_urls(text or "")
    url_context: str | None = None
    if urls_in_text and not media_id:
        # Text-only message with URL(s): pass URLs explicitly so GPT can classify
        url_lines = "\n".join(f"• {u}" for u in urls_in_text[:5])
        url_context = f"[روابط مُرسَلة]\n{url_lines}"
        logger.info(
            "[URL RECEIVED] teacher_id=%d urls=%d first=%s",
            teacher.id, len(urls_in_text), urls_in_text[0][:80],
        )

    # ── Determine what to pass to GPT Vision ─────────────────────────────────
    # - Images:    storage_path in base64
    # - Videos:    thumbnail_path if extracted
    # - Audio/URL/Docs: no visual
    gpt_storage_path: str | None = None
    if evidence_type == "image":
        gpt_storage_path = storage_path
    elif is_video_msg and thumbnail_path:
        gpt_storage_path = thumbnail_path

    # Compose text for GPT: original text + URL context if any
    gpt_text = "\n".join(filter(None, [text, url_context])) or None

    # ── Ask GPT — sole decision-maker and sole speaker ───────────────────────
    decision = await ask_gpt(
        text=gpt_text,
        teacher_context=teacher_context,
        storage_path=gpt_storage_path,
        image_url=None,       # NEVER pass WA URLs — they require auth GPT cannot provide
        mime_type="image/jpeg" if (is_video_msg and thumbnail_path) else mime_type,
        file_name=safe_filename or file_name,
        transcript=transcript,
        is_video=is_video_msg,
        is_pdf=_is_pdf_msg,   # Use correct label so GPT doesn't confuse PDF with voice
    )

    # ── CRITICAL PDF SAFETY: override GPT flags that could lose a PDF ─────────
    # A PDF file can NEVER be a "system instruction" — it's always a real document.
    # GPT may misclassify the extracted PDF content (especially our structured analysis
    # output) as a system command. We hard-override to prevent silent data loss.
    if _is_pdf_msg and media_id:
        if decision.get("is_system_instruction"):
            logger.warning(
                "[PDF SAFETY] teacher_id=%d GPT incorrectly flagged PDF as system_instruction "
                "— overriding to is_system_instruction=False, should_save=True",
                teacher.id,
            )
            decision = {
                **decision,
                "is_system_instruction": False,
                "should_save": True,
                "intent": "evidence",
            }
        elif not decision.get("should_save"):
            # GPT set should_save=False for a PDF — likely misclassification. Force it.
            logger.warning(
                "[PDF SAFETY] teacher_id=%d GPT returned should_save=False for PDF (intent=%s) "
                "— forcing should_save=True",
                teacher.id, decision.get("intent"),
            )
            decision = {
                **decision,
                "should_save": True,
                "intent": "evidence",
                "_force_saved": True,
            }

    logger.info(
        "[GPT DECISION] teacher_id=%d intent=%s should_save=%s confidence=%.2f title=%r "
        "is_system_instruction=%s is_low_quality=%s is_lesson_plan=%s reply_style=%s",
        teacher.id, decision["intent"], decision["should_save"],
        decision["confidence"], decision["title"],
        decision.get("is_system_instruction"), decision.get("is_low_quality"),
        decision.get("is_lesson_plan"), decision.get("reply_style"),
    )

    intent = decision["intent"]
    reply  = decision["reply"]

    # ── Apply profile_update from ANY GPT intent ─────────────────────────────
    # GPT may save an audio/text as evidence and still extract profile fields
    # from it (subject, grades, school, principal, region, education_admin).
    # Persist those fields before routing/export so the PDF sees fresh data.
    profile_update = _clean_profile_update(decision.get("profile_update"))
    if profile_update:
        # ── Name confirmation gate ────────────────────────────────────────────
        # If the update includes a "name" field AND the message came via audio
        # (Whisper transcript) OR PDF content, ask the teacher to confirm.
        # Whisper often mishears Arabic names (عايد → عائد, الحارثي → الحارفي).
        # PDFs may contain the wrong name spelling in the document itself.
        # If the teacher already has a confirmed name, never override it from media/PDF.
        pending_name: str | None = None
        if "name" in profile_update:
            existing_name = (teacher.name or "").strip()
            if _is_pdf_msg:
                # Never extract teacher name from PDF content — discard silently
                profile_update.pop("name")
                logger.info(
                    "[NAME BLOCKED] teacher_id=%d — ignoring name from PDF content (existing=%r)",
                    teacher.id, existing_name,
                )
            elif transcript:
                pending_name = profile_update.pop("name")  # remove from immediate save
                # If teacher already has a confirmed name, don't replace without explicit confirmation
                if existing_name and existing_name == pending_name:
                    # Same name — no need to confirm again, put it back for immediate save
                    profile_update["name"] = pending_name
                    pending_name = None
                else:
                    _PENDING_NAME_CONFIRMATION[teacher.id] = pending_name
                    logger.info(
                        "[NAME PENDING CONFIRMATION] teacher_id=%d raw_name=%r (from audio)",
                        teacher.id, pending_name,
                    )

        # Apply all other profile fields immediately
        if profile_update:
            update_teacher(db, teacher, profile_update)
            _LAST_PROFILE_UPDATES[teacher.id] = profile_update
            logger.info(
                "[PROFILE UPDATED] teacher_id=%d fields=%s source_intent=%s",
                teacher.id, list(profile_update.keys()), intent,
            )

        if pending_name:
            # Phase-6: standardized name confirmation message with consistent tone.
            reply = wa_integration.make_name_confirmation_question(pending_name)
        elif intent == "update_profile":
            reply = _format_profile_update_reply(profile_update)

    # ── FORCE SAVE for any media message ─────────────────────────────────────
    # Golden rule: if WhatsApp media was received (image/video/audio/document),
    # it MUST be saved as evidence regardless of GPT's intent decision.
    # GPT still provides title, category and reply. Only the save is forced.
    _MEDIA_DEFAULT_CATEGORY: dict[str, str] = {
        "image":    "نشاط صفي",
        "video":    "نشاط صفي",
        "audio":    "نشاط صفي",
        "document": "ملف إداري",
        "pdf":      "ملف إداري",
        "url":      "رابط إثرائي",
    }

    # AI-first: if GPT explicitly identified this as a system instruction,
    # never force-save it — even if media was attached. GPT's semantic judgment
    # overrides the blanket media-save rule.
    _is_system_instruction = bool(decision.get("is_system_instruction", False))
    if _is_system_instruction:
        logger.info(
            "[SYSTEM INSTRUCTION] teacher_id=%d — GPT flagged as system command, skipping save. "
            "intent=%s confidence=%.2f",
            teacher.id, intent, decision.get("confidence", 0.0),
        )

    if media_id and not decision["should_save"] and intent not in ("failure",) and not _is_system_instruction:
        # GPT classified this as non-evidence (e.g. smalltalk), but media exists → force save.
        # EXCEPTION: if GPT explicitly says is_system_instruction, never force-save.
        forced_cat   = decision["category"] or _MEDIA_DEFAULT_CATEGORY.get(evidence_type, "نشاط صفي")
        forced_title = decision["title"] or f"شاهد {evidence_type}"
        logger.warning(
            "[FORCE SAVE] teacher_id=%d — media detected but GPT intent=%s should_save=False. "
            "Forcing save with category=%r title=%r",
            teacher.id, intent, forced_cat, forced_title,
        )
        decision = {
            **decision,
            "intent":      "evidence",
            "should_save": True,
            "category":    forced_cat,
            "title":       forced_title,
            "_force_saved": True,
        }
        intent = "evidence"

    # ── Intent routing ────────────────────────────────────────────────────────

    if intent == "update_profile":
        # Profile updates were already applied above for all intents.
        # GPT already wrote the reply (e.g. "تم حفظ اسمك يا تركي 🌿")
        pass

    elif intent in ("my_files", "my_data", "edit_data", "smalltalk", "help", "batch_summary"):
        # GPT writes the reply based on context. Backend does nothing extra.
        # batch_summary: GPT already wrote the full batch report in its reply.
        pass

    elif intent == "payment":
        logger.info("[EXPORT REQUESTED] teacher_id=%d sub_status=%s", teacher.id, sub_info["status"])

        if not sub_active:
            # Not active_paid → send payment button, block export
            logger.info(
                "[EXPORT BLOCKED] teacher_id=%d reason=subscription_status_%s",
                teacher.id, sub_info["status"],
            )
            try:
                payment_url = await _create_moyasar_link(
                    db, teacher.id, teacher.name or "", teacher.phone or ""
                )
            except Exception as exc:
                logger.error("Moyasar invoice creation failed: %s", exc)
                payment_url = get_payment_link(teacher.id)

            body_text = build_payment_link_message(payment_url, teacher.name or "")

            # Interactive CTA button — not a plain text URL
            background_tasks.add_task(
                send_whatsapp_message,
                teacher.phone,
                body_text,
                teacher_id=teacher.id,
                context="payment_link",
            )
            return {
                "ok": False,
                "teacher_id": teacher.id,
                "intent": intent,
                "reason": f"subscription_{sub_info['status']}",
                "payment_url": payment_url,
            }

        # active_paid → show 2-button card: مراجعة الملف | تصدير الآن
        logger.info("[EXPORT MODE REQUESTED] teacher_id=%d evidence_count=%d", teacher.id, evidence_count)

        from app.services.teachers import get_or_create_review_token
        review_token = get_or_create_review_token(db, teacher)
        review_url   = f"{settings.effective_base_url}/review/{review_token}"

        # Phase-6: warn about duplicates / low-confidence before the 2-button card.
        _gpt_export_warning = wa_integration.make_pre_export_warning(
            get_teacher_evidences(db, teacher.id),
            teacher_id=teacher.id,
            teacher_name=teacher.name,
            base_url=settings.effective_base_url,
        )
        if _gpt_export_warning:
            background_tasks.add_task(
                send_whatsapp_message, teacher.phone, _gpt_export_warning,
                teacher_id=teacher.id, context="pre_export_warning",
            )
        _AWAITING_EXPORT_CHOICE.add(teacher.id)
        background_tasks.add_task(
            send_pre_export_choice_buttons,
            teacher.phone,
            review_url,
            teacher_id=teacher.id,
        )
        return {"ok": True, "teacher_id": teacher.id, "intent": intent, "awaiting_review_or_export": True}

    elif decision["should_save"]:
        # GPT decided to save evidence (intents: evidence | batch_save | url_link)
        # url_link: evidence_type comes from detect_evidence_type (already "url" for URL-only msgs)
        # batch_save: same flow as evidence, but reply is intentionally short (GPT handles it)
        from app.services.evidences import ALLOWED_CATEGORIES

        ev_type = evidence_type
        if intent == "url_link" and ev_type == "text":
            ev_type = "url"   # ensure URL text messages are stored as "url" type

        # Normalise category: if GPT returned an unknown category, use the closest default.
        # For PDFs: if pre-analysis produced a high-confidence category, prefer it when
        # the main GPT returned a generic/wrong category or had low confidence.
        gpt_category = (decision["category"] or "").strip()
        if not gpt_category or gpt_category not in ALLOWED_CATEGORIES:
            fallback_cat = _MEDIA_DEFAULT_CATEGORY.get(ev_type, "نشاط صفي")
            if gpt_category and gpt_category not in ALLOWED_CATEGORIES:
                logger.info(
                    "[CATEGORY FIXED] teacher_id=%d GPT category %r not in allowed list → %r",
                    teacher.id, gpt_category, fallback_cat,
                )
            # PDF with pre-analysis: prefer its category over a generic fallback
            if ev_type == "pdf" and _pdf_preanalysis:
                _pre_cat = (_pdf_preanalysis.get("category") or "").strip()
                _pre_conf = float(_pdf_preanalysis.get("confidence") or 0)
                # Accept categories from both ALLOWED_CATEGORIES and _MAIN_CATEGORY_ORDER
                _PDF_ALL_VALID_CATS = set(ALLOWED_CATEGORIES) | {
                    "التخطيط", "سجل المتابعة", "التقويم", "التحفيز",
                    "إدارة الصف", "التواصل", "مصادر تعليمية",
                    "ملفات إدارية", "روابط إثرائية",
                }
                _GENERIC_CATS = {"ملفات إدارية", "ملف إداري", "أخرى", ""}
                if _pre_cat and _pre_cat in _PDF_ALL_VALID_CATS and _pre_conf >= 0.55:
                    if _pre_cat not in _GENERIC_CATS:
                        gpt_category = _pre_cat
                        logger.info(
                            "[PDF CAT OVERRIDE] teacher_id=%d using pre-analysis cat=%r conf=%.2f",
                            teacher.id, _pre_cat, _pre_conf,
                        )
                    else:
                        gpt_category = fallback_cat
                else:
                    gpt_category = fallback_cat
            else:
                gpt_category = fallback_cat

        # PDF: if pre-analysis has higher-confidence category than main GPT's generic result,
        # allow the pre-analysis to override (content-based beats generic fallback).
        elif ev_type == "pdf" and _pdf_preanalysis:
            _pre_cat  = (_pdf_preanalysis.get("category") or "").strip()
            _pre_conf = float(_pdf_preanalysis.get("confidence") or 0)
            _gpt_conf = float(decision.get("confidence") or 0)
            # Broad valid categories (includes _MAIN_CATEGORY_ORDER not just ALLOWED_CATEGORIES)
            _PDF_ALL_VALID_CATS = set(ALLOWED_CATEGORIES) | {
                "التخطيط", "سجل المتابعة", "التقويم", "التحفيز",
                "إدارة الصف", "التواصل", "مصادر تعليمية",
                "ملفات إدارية", "روابط إثرائية",
            }
            _GENERIC_CATS = {"ملفات إدارية", "ملف إداري", "أخرى", ""}
            # Override when GPT returned a generic category but pre-analysis is more specific
            if (
                _pre_cat
                and _pre_cat in _PDF_ALL_VALID_CATS
                and _pre_cat not in _GENERIC_CATS
                and (
                    gpt_category in _GENERIC_CATS          # GPT chose generic → always prefer pre
                    or (_pre_conf >= 0.75 and _gpt_conf < 0.80 and _pre_cat != gpt_category)
                )
            ):
                logger.info(
                    "[PDF CAT OVERRIDE] teacher_id=%d pre=%r(%.2f) > gpt=%r(%.2f)",
                    teacher.id, _pre_cat, _pre_conf, gpt_category, _gpt_conf,
                )
                gpt_category = _pre_cat

        # Phase-6: apply teacher-provided category hint when the AI fell back to
        # a generic category. Never overrides a specific GPT or pre-analysis choice.
        _teacher_hint = _PENDING_CATEGORY_HINT.get(teacher.id)
        _GENERIC_FALLBACKS = {"ملفات إدارية", "ملف إداري", "نشاط صفي", "أخرى", ""}
        if _teacher_hint and gpt_category in _GENERIC_FALLBACKS:
            logger.info(
                "[CATEGORY HINT APPLIED] teacher_id=%d hint=%r overrides fallback=%r",
                teacher.id, _teacher_hint, gpt_category,
            )
            gpt_category = _teacher_hint

        # PDF: enrich title/description from pre-analysis if GPT title is too generic
        _gpt_title = decision["title"] or ""
        _gpt_desc  = decision.get("description") or ""
        if ev_type == "pdf" and _pdf_preanalysis:
            _pre_title = (_pdf_preanalysis.get("title") or "").strip()
            _pre_desc  = (_pdf_preanalysis.get("description") or "").strip()
            _pre_conf  = float(_pdf_preanalysis.get("confidence") or 0)
            # Use pre-analysis title when it's more specific and GPT's is too short/generic
            if _pre_title and (not _gpt_title or len(_gpt_title) < 5 or _pre_conf >= 0.80):
                _gpt_title = _pre_title
            if _pre_desc and (not _gpt_desc or len(_gpt_desc) < 10):
                _gpt_desc = _pre_desc

        # ── Pre-save deduplication checks ─────────────────────────────────────
        _content_hash: str | None = media_hash   # already computed for media
        _skip_dedup = False

        if not _content_hash:
            # Text / URL evidence: compute hash for dedup
            if ev_type == "url":
                # Use first URL found, or the full text
                _first_url = (extract_urls(gpt_text or text or "") or [None])[0]
                if _first_url:
                    _content_hash = hash_url(_first_url)
            elif ev_type == "text" and (transcript or gpt_text or text):
                _raw_text = transcript or gpt_text or text or ""
                _content_hash = hash_text(_raw_text)
                # Also run near-duplicate similarity check for text
                if not is_exact_duplicate(db, teacher.id, _content_hash):
                    _near_dup = find_near_duplicate_text(db, teacher.id, _raw_text)
                    if _near_dup:
                        logger.info(
                            "[DUPLICATE SKIPPED] near-duplicate text teacher_id=%d similar_to_id=%d",
                            teacher.id, _near_dup.id,
                        )
                        _skip_dedup = True

        if not _skip_dedup and _content_hash and is_exact_duplicate(db, teacher.id, _content_hash):
            logger.info(
                "[DUPLICATE SKIPPED] exact hash match teacher_id=%d type=%s hash=%s",
                teacher.id, ev_type, _content_hash[:12],
            )
            _skip_dedup = True

        if _skip_dedup:
            # Duplicate detected — override reply with duplicate notification,
            # but do NOT create a new evidence record.
            logger.info("[DUPLICATE SKIPPED] no evidence created teacher_id=%d type=%s", teacher.id, ev_type)
            if media_id and ev_type not in ("text", "url"):
                # For media duplicates found at pre-save check: look up existing record
                _dup2_ev = get_evidence_by_hash(db, teacher.id, _content_hash) if _content_hash else None
                reply = build_file_saved_message(
                    ev_type=ev_type,
                    category=(_dup2_ev.category if _dup2_ev else gpt_category),
                    title=(_dup2_ev.title if _dup2_ev else _gpt_title or ""),
                    is_duplicate=True,
                )
        else:
            try:
                # Provide teacher context for deep AI enrichment
                set_enrichment_teacher_context(
                    name=teacher.name,
                    subject=teacher.subject,
                    stage=teacher.stage,
                    grades=teacher.grades,
                    school_name=teacher.school_name,
                )
                is_force_saved = bool(decision.get("_force_saved"))
                if ev_type == "pdf":
                    logger.info("[PDF DB SAVE START] teacher_id=%d (gpt success path)", teacher.id)

                evidence = create_evidence(
                    db=db,
                    teacher_id=teacher.id,
                    source_phone=teacher.phone,
                    evidence_type=ev_type,
                    # For audio/video: save transcript. For URL: save the raw text (contains URL).
                    # For image/doc: caption/text.
                    message_text=transcript or gpt_text or text,
                    media_url=media_url,
                    # For videos, store the extracted thumbnail for PDF rendering.
                    # The original video is not embeddable in PDF, but the thumbnail is.
                    storage_path=thumbnail_path if (ev_type == "video" and thumbnail_path) else storage_path,
                    file_name=safe_filename or file_name,
                    mime_type=mime_type,
                    category=gpt_category,
                    title=_gpt_title or f"شاهد {ev_type}",
                    description=_gpt_desc or decision.get("description"),
                    grade=decision.get("grade"),
                    subject=decision.get("subject") or (
                        _pdf_preanalysis.get("subject") if _pdf_preanalysis else None
                    ),
                    content_hash=_content_hash,
                    # force_saved = GPT didn't decide to save, system overrode.
                    # Exporter normalises these automatically before PDF generation.
                    ai_status="force_saved" if is_force_saved else "completed",
                    ai_raw=dict(decision),
                )

                # ── Verify PDF is actually visible to export pipeline ─────────
                if ev_type == "pdf":
                    _is_visible = verify_evidence_in_export(db, evidence.id, teacher.id)
                    if not _is_visible:
                        logger.error(
                            "[PDF DB SAVE FAILED] teacher_id=%d id=%d not visible in export query after save",
                            teacher.id, evidence.id,
                        )
                        raise RuntimeError("PDF saved but not visible in export query")
                    logger.info(
                        "[PDF DB SAVE SUCCESS] teacher_id=%d id=%d visible_in_export=True",
                        teacher.id, evidence.id,
                    )
                    # ── Generate PDF first-page preview image eagerly in background ──
                    if storage_path:
                        _pdf_sp = storage_path
                        background_tasks.add_task(generate_pdf_preview, _pdf_sp)

                if ev_type == "pdf":
                    log_tag = "[PDF SAVED]"
                elif intent == "evidence":
                    log_tag = "[EVIDENCE SAVED]"
                else:
                    log_tag = f"[{intent.upper()} SAVED]"
                logger.info(
                    "%s teacher_id=%d evidence_id=%d type=%s category=%r title=%r confidence=%.2f",
                    log_tag, teacher.id, evidence.id, ev_type,
                    gpt_category, decision["title"], decision["confidence"],
                )
                # ── Structured save confirmation for all media evidence ────────
                # Phase-6: uses make_save_reply (build_evidence_saved_smart) which
                # includes importance score and a review hint when confidence is low.
                if media_id and ev_type not in ("text", "url"):
                    reply = wa_integration.make_save_reply(
                        ev_type=ev_type,
                        category=gpt_category,
                        title=_gpt_title or None,
                        confidence=decision.get("confidence"),
                        ai_raw=dict(decision),
                    )
                    # Clear any pending category hint now that the save completed.
                    _PENDING_CATEGORY_HINT.pop(teacher.id, None)
            except Exception as save_exc:
                logger.error(
                    "[SAVE FAILED] teacher_id=%d type=%s category=%r error=%s",
                    teacher.id, ev_type, gpt_category, save_exc, exc_info=True,
                )
                if _is_pdf_msg:
                    logger.error("[PDF FAILED] teacher_id=%d — save exception: %s", teacher.id, save_exc)

    elif intent == "failure":
        # GPT failed after all retries.
        # CRITICAL: If this was a PDF, save it first before retrying — never lose a PDF.
        if _is_pdf_msg and storage_path and not _skip_dedup:
            try:
                set_enrichment_teacher_context(
                    name=teacher.name, subject=teacher.subject,
                    stage=teacher.stage, grades=teacher.grades,
                    school_name=teacher.school_name,
                )
                # Prefer pre-analysis results if available; fall back to filename inference
                if _pdf_preanalysis:
                    _pdf_fallback_title = (
                        _pdf_preanalysis.get("title")
                        or (file_name.replace(".pdf", "").replace("_", " ").replace("-", " ") if file_name else "ملف PDF")
                    )
                    _pdf_fallback_cat = (
                        _pdf_preanalysis.get("category")
                        or _infer_pdf_category_from_name(file_name or safe_filename or "")
                    )
                    _pdf_fallback_desc = (
                        _pdf_preanalysis.get("description")
                        or "ملف PDF تم حفظه — تعذّر التحليل التلقائي."
                    )
                else:
                    _pdf_fallback_title = (
                        file_name.replace(".pdf", "").replace("_", " ").replace("-", " ")
                        if file_name else "ملف PDF"
                    )
                    _pdf_fallback_cat = _infer_pdf_category_from_name(file_name or safe_filename or "")
                    _pdf_fallback_desc = "ملف PDF تم حفظه — تعذّر التحليل التلقائي."

                logger.info("[PDF DB SAVE START] teacher_id=%d (force-save path)", teacher.id)
                _saved_ev = create_evidence(
                    db=db,
                    teacher_id=teacher.id,
                    source_phone=teacher.phone,
                    evidence_type="pdf",
                    message_text=transcript or gpt_text or text,
                    media_url=media_url,
                    storage_path=storage_path,
                    file_name=safe_filename or file_name,
                    mime_type=mime_type,
                    category=_pdf_fallback_cat,
                    title=_pdf_fallback_title,
                    description=_pdf_fallback_desc,
                    content_hash=media_hash,
                    ai_status="fallback",
                )
                # ── Verify the row is actually persisted and visible to export ────
                _is_visible = verify_evidence_in_export(db, _saved_ev.id, teacher.id)
                if not _is_visible:
                    logger.error(
                        "[PDF DB SAVE FAILED] teacher_id=%d id=%d not visible in export query after save",
                        teacher.id, _saved_ev.id,
                    )
                    raise RuntimeError("PDF saved but not visible in export query")

                logger.info(
                    "[PDF DB SAVE SUCCESS] teacher_id=%d id=%d cat=%r title=%r src=%s",
                    teacher.id, _saved_ev.id, _pdf_fallback_cat, _pdf_fallback_title,
                    "preanalysis" if _pdf_preanalysis else "filename",
                )
                # Generate PDF preview image in background eagerly
                if storage_path:
                    background_tasks.add_task(generate_pdf_preview, storage_path)
                background_tasks.add_task(
                    send_whatsapp_message,
                    teacher.phone,
                    build_file_saved_message(
                        "pdf",
                        _pdf_fallback_cat,
                        _pdf_fallback_title,
                        analysis_failed=True,
                    ),
                    teacher_id=teacher.id,
                    context="pdf_saved_no_analysis",
                )
            except Exception as pdf_save_exc:
                logger.error("[PDF FAILED] teacher_id=%d save error: %s", teacher.id, pdf_save_exc)
                background_tasks.add_task(
                    send_whatsapp_message,
                    teacher.phone,
                    "حدث خطأ أثناء حفظ الملف، أعد إرساله مرة أخرى 🙏",
                    teacher_id=teacher.id,
                    context="pdf_save_error",
                )
        else:
            # Non-PDF failure: send interim + schedule retry
            background_tasks.add_task(
                send_whatsapp_message, teacher.phone, reply, teacher_id=teacher.id
            )
            background_tasks.add_task(
                _gpt_retry_and_reply,
                teacher.id,
                teacher.phone,
                text,
                teacher_context,
                storage_path if evidence_type == "image" else None,
                None,  # image_url=None — WA URLs are auth-protected, base64 only
                mime_type,
                safe_filename or file_name,
            )
        return {"ok": False, "teacher_id": teacher.id, "intent": "failure", "retrying": not _is_pdf_msg}

    else:
        logger.info("[EVIDENCE SKIPPED] teacher_id=%d intent=%s", teacher.id, intent)

    # ── Progressive onboarding: short, contextual and one-time ────────────────
    # Mutate the final reply only when the onboarding/context rule is stronger
    # than GPT's generic answer.
    now = datetime.now(timezone.utc)
    is_successful = intent not in ("failure",)
    is_voice_success = msg_type in ("audio", "voice") and bool(transcript)
    is_media_without_description = (
        bool(media_id)
        and msg_type in ("image", "video", "document")
        and not (text or transcript)
    )

    if is_successful and is_voice_success and not teacher.first_voice_processed_at:
        reply = _FIRST_VOICE_SUCCESS_MSG
        teacher.first_voice_processed_at = now
        logger.info("[ONBOARDING] first voice processed teacher_id=%d", teacher.id)

    elif is_successful and is_media_without_description and not teacher.media_hint_sent_at:
        reply = _MEDIA_NO_DESCRIPTION_HINT_MSG
        teacher.media_hint_sent_at = now
        logger.info("[ONBOARDING] media hint marked teacher_id=%d", teacher.id)

    elif is_successful and is_new_user and not media_id:
        # First text-only interaction: keep it very short, no marketing wall.
        reply = _WELCOME_SHORT_MSG

    # ── Send reply ────────────────────────────────────────────────────────────
    background_tasks.add_task(
        send_whatsapp_message, teacher.phone, reply, teacher_id=teacher.id
    )

    if is_successful and is_new_user:
        teacher.welcomed = True
        teacher.welcome_sent_at = now
        logger.info("[ONBOARDING] welcome marked teacher_id=%d", teacher.id)

    # Voice hint: once, after first/second successful non-voice interaction.
    if (
        is_successful
        and not teacher.voice_hint_sent_at
        and not teacher.first_voice_processed_at
        and not is_voice_success
        and evidence_count <= 1
    ):
        teacher.voice_hint_sent_at = now
        background_tasks.add_task(
            send_whatsapp_message,
            teacher.phone,
            _VOICE_HINT_MSG,
            teacher_id=teacher.id,
            context="onboarding_voice_hint",
        )
        logger.info("[ONBOARDING] voice hint scheduled teacher_id=%d", teacher.id)

    db.commit()

    return {"ok": True, "teacher_id": teacher.id, "intent": intent, "reply": reply}


# ─── Moyasar Payment Webhook (POST) ──────────────────────────────────────────

async def _process_moyasar_payment(
    payment_id: str,
    invoice_id: str,
    status: str,
    amount_halalah: int,
    service: str,
    teacher_id: int,
    plan_slug: str,
    raw_data: dict,
) -> None:
    """
    Background task: validate → upsert PaymentAttempt → activate subscription → send receipt.
    Opens its own DB session to avoid DetachedInstanceError.
    Never raises — webhook caller always gets 200.

    Activation flow:
      1. Upsert PaymentAttempt (find by payment_id or invoice_id, or create new).
      2. Validate service / status / amount.
      3. Activate TeacherSubscription with payment_reference = payment_id.
      4. Send WhatsApp receipt (session message, no template).
         If 24h window expired → log [PAYMENT WHATSAPP SEND FAILED], do NOT block activation.
    """
    from datetime import datetime, timezone
    from app.db.base import SessionLocal

    db = SessionLocal()
    try:
        # ── Guard 1: service ──────────────────────────────────────────────────
        if service and service != moyasar_svc.SHAWAHID_SERVICE_ID:
            logger.warning(
                "[PAYMENT] service='%s' != '%s' — skipping | payment_id=%s teacher_id=%d",
                service, moyasar_svc.SHAWAHID_SERVICE_ID, payment_id, teacher_id,
            )
            return

        # ── Guard 2: status ───────────────────────────────────────────────────
        if status != "paid":
            logger.info("[PAYMENT] payment_id=%s status=%s — no action", payment_id, status)
            return

        # ── Guard 3: amount ───────────────────────────────────────────────────
        if amount_halalah < settings.SHAWAHID_LAUNCH_PRICE_HALALAH:
            logger.warning(
                "[PAYMENT] payment_id=%s amount=%d halalah < required %d — skipping",
                payment_id, amount_halalah, settings.SHAWAHID_LAUNCH_PRICE_HALALAH,
            )
            return

        # ── Guard 4: teacher exists ───────────────────────────────────────────
        teacher = get_teacher_by_id(db, teacher_id)
        if not teacher:
            logger.error("[PAYMENT] teacher_id=%d not found — skipping", teacher_id)
            return

        # ── Upsert PaymentAttempt ────────────────────────────────────────────
        # Use invoice_id as the canonical key (matches what we stored at invoice creation).
        # For invoice events: payment_id == invoice_id.
        # For payment events: payment_id is pmt_xxx; invoice_id points to our stored record.
        canonical_id = invoice_id or payment_id
        pa = upsert_paid_payment_attempt(
            db=db,
            teacher_id=teacher_id,
            provider_payment_id=canonical_id,
            amount_sar=float(amount_halalah) / 100,
            raw_response=raw_data,
            metadata=raw_data.get("metadata") or {},
        )
        logger.info(
            "[PAYMENT] PaymentAttempt id=%d canonical_id=%s status=paid",
            pa.id, canonical_id,
        )

        # ── Activate subscription ─────────────────────────────────────────────
        paid_at = datetime.now(timezone.utc)
        sub = activate_subscription(
            db=db,
            teacher_id=teacher_id,
            payment_provider="moyasar",
            payment_reference=canonical_id,   # must match PA.provider_payment_id
            amount_sar=float(amount_halalah) / 100,
            plan_slug=plan_slug,
        )
        logger.info(
            "[PAYMENT ACTIVATED] teacher_id=%d payment_id=%s sub_ends_at=%s",
            teacher_id, canonical_id, sub.ends_at,
        )

        # ── Build & send receipt via WhatsApp ─────────────────────────────────
        from app.services.moyasar import _teacher_display_name
        display_name = _teacher_display_name(teacher.name or "", teacher.phone or "")

        receipt_msg = build_payment_receipt_message(
            teacher_display_name=display_name,
            teacher_phone=teacher.phone or "",
            provider_payment_id=canonical_id,
            paid_at=paid_at,
            amount_sar=settings.SHAWAHID_LAUNCH_PRICE_SAR,
            plan_slug=plan_slug,
            starts_at=sub.starts_at,
            ends_at=sub.ends_at,
        )

        logger.info("[PAYMENT RECEIPT SEND STARTED] teacher_id=%d", teacher_id)
        sent = await send_whatsapp_message(
            teacher.phone,
            receipt_msg,
            teacher_id=teacher_id,
            context="payment_receipt",
        )

        if sent:
            logger.info(
                "[PAYMENT RECEIPT SENT] teacher_id=%d payment_id=%s",
                teacher_id, canonical_id,
            )
        else:
            logger.warning(
                "[PAYMENT WHATSAPP SEND FAILED] teacher_id=%d payment_id=%s reason=see_above",
                teacher_id, canonical_id,
            )

    except Exception as exc:
        logger.error(
            "[PAYMENT ERROR] processing payment_id=%s teacher_id=%d: %s",
            payment_id, teacher_id, exc, exc_info=True,
        )
    finally:
        db.close()


@router.post("/webhook/payment")
async def payment_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Moyasar webhook endpoint.
    Always returns 200 — never 4xx after signature check passes.
    Scheduling happens in background so Moyasar doesn't retry.
    """
    raw_body = await request.body()

    # Log BEFORE signature check so we know the webhook arrived
    logger.info(
        "[MOYASAR WEBHOOK RECEIVED] path=/webhook/payment "
        "content_length=%d has_signature=%s",
        len(raw_body),
        bool(request.headers.get("Moyasar-Signature")),
    )

    # ── Signature verification ────────────────────────────────────────────────
    # NOTE: Moyasar's callback_url (per-invoice) may not include the same
    # Moyasar-Signature header as global dashboard webhooks.
    # We log the failure but do NOT return 403 — that would cause Moyasar to
    # retry indefinitely and never activate the subscription.
    # Security is enforced by metadata validation (service, amount, teacher_id).
    signature = request.headers.get("Moyasar-Signature", "")
    sig_ok = moyasar_svc.verify_webhook_signature(raw_body, signature)
    if not sig_ok:
        logger.warning(
            "[MOYASAR WEBHOOK SIG WARNING] signature check failed "
            "| has_signature=%s | continuing (callback_url payments may be unsigned) "
            "| tip: set MOYASAR_VERIFY_SIGNATURES=false to suppress",
            bool(signature),
        )

    # ── Parse JSON ────────────────────────────────────────────────────────────
    try:
        payload = await request.json()
    except Exception:
        logger.error("[MOYASAR WEBHOOK] invalid JSON body")
        raise HTTPException(status_code=400, detail="Invalid JSON")

    logger.info(
        "[MOYASAR WEBHOOK PARSED] type=%r top_keys=%s",
        payload.get("type"),
        list(payload.keys())[:8],
    )

    parsed = moyasar_svc.parse_webhook_payload(payload)

    # ── Fallback: no teacher_id from metadata — try DB lookup by invoice_id ──
    if parsed and not parsed["teacher_id"]:
        invoice_id = parsed["invoice_id"]
        if invoice_id:
            from app.models.payment_attempt import PaymentAttempt as _PA
            pa_match = (
                db.query(_PA)
                .filter(_PA.provider_payment_id == invoice_id)
                .first()
            )
            if pa_match:
                parsed["teacher_id"] = pa_match.teacher_id
                logger.info(
                    "[MOYASAR WEBHOOK FALLBACK] resolved teacher_id=%d from invoice_id=%s",
                    pa_match.teacher_id, invoice_id,
                )
            else:
                logger.warning(
                    "[MOYASAR WEBHOOK] no PaymentAttempt found for invoice_id=%s — cannot resolve teacher",
                    invoice_id,
                )

    if not parsed or not parsed["teacher_id"]:
        logger.info("[MOYASAR WEBHOOK] no usable teacher context — skipping | payload_type=%r", payload.get("type"))
        return {"ok": True, "skipped": True}

    background_tasks.add_task(
        _process_moyasar_payment,
        payment_id=parsed["payment_id"],
        invoice_id=parsed["invoice_id"],
        status=parsed["status"],
        amount_halalah=parsed["amount_halalah"],
        service=parsed["service"],
        teacher_id=parsed["teacher_id"],
        plan_slug=parsed["plan_slug"],
        raw_data=parsed["raw"],
    )

    logger.info(
        "[MOYASAR WEBHOOK QUEUED] payment_id=%s teacher_id=%d status=%s amount=%d",
        parsed["payment_id"], parsed["teacher_id"], parsed["status"], parsed["amount_halalah"],
    )
    return {"ok": True}


# ─── Payment Success Page (GET) ───────────────────────────────────────────────

@router.get("/payment/success", response_class=HTMLResponse)
async def payment_success(request: Request):
    """
    Redirect target after Moyasar checkout completes.
    Shown to teacher after completing payment on Moyasar hosted page.
    Activation is handled by the webhook — NOT this page.
    """
    teacher_id = request.query_params.get("teacher_id", "")
    trust = settings.trust_info
    html = f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>شواهد AI — تم استلام الدفع</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'Segoe UI', Tahoma, Arial, sans-serif;
      background: #f8fafc;
      color: #1e293b;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 24px;
    }}
    .card {{
      background: #fff;
      border-radius: 16px;
      box-shadow: 0 4px 24px rgba(0,0,0,.08);
      padding: 48px 40px;
      max-width: 480px;
      width: 100%;
      text-align: center;
    }}
    .icon {{ font-size: 64px; margin-bottom: 24px; }}
    h1 {{ font-size: 1.6rem; margin-bottom: 12px; color: #0f172a; }}
    p {{ font-size: 1rem; color: #475569; line-height: 1.7; margin-bottom: 16px; }}
    .highlight {{
      background: #f0fdf4;
      border: 1px solid #bbf7d0;
      border-radius: 10px;
      padding: 16px 20px;
      color: #166534;
      font-size: .95rem;
      margin-top: 20px;
    }}
    .wa-btn {{
      display: inline-block;
      margin-top: 28px;
      background: #25d366;
      color: #fff;
      padding: 12px 28px;
      border-radius: 8px;
      text-decoration: none;
      font-size: 1rem;
      font-weight: 600;
    }}
    .trust-box {{
      margin-top: 22px;
      text-align: right;
      background: #f8fafc;
      border: 1px solid #e2e8f0;
      border-radius: 12px;
      padding: 16px 18px;
      color: #334155;
      font-size: .9rem;
      line-height: 1.8;
    }}
    .trust-title {{
      font-weight: 800;
      color: #0f172a;
      margin-bottom: 8px;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">✅</div>
    <h1>تم استلام عملية الدفع</h1>
    <p>
      تم استلام عملية الدفع. سيتم تفعيل اشتراكك تلقائيًا خلال لحظات.
      في حال احتجت أي مساعدة يمكنك التواصل معنا عبر الدعم الرسمي.
    </p>
    <div class="highlight">
      يمكنك العودة إلى واتساب وكتابة:<br>
      <strong style="font-size:1.1rem;letter-spacing:.05em;">تصدير</strong><br>
      لإنشاء ملف الشواهد PDF فور تفعيل الاشتراك.
    </div>
    <div class="trust-box">
      <div class="trust-title">ضمانات الثقة</div>
      <div>✅ الخدمة مقدمة من {trust["provider"]}</div>
      <div>✅ السجل التجاري موثق</div>
      <div>✅ {trust["business_verification_text"]}</div>
      <div>🌐 الموقع الرسمي: <a href="{trust["website"]}">{trust["website"]}</a></div>
      <div>📩 الدعم: <a href="mailto:{trust["support_email"]}">{trust["support_email"]}</a></div>
      <div>📱 للاستفسار: {trust["support_person"]} — <span dir="ltr">{trust["support_phone"]}</span></div>
    </div>
    <a href="https://wa.me/{trust["support_phone"]}" class="wa-btn">التواصل عبر واتساب</a>
  </div>
</body>
</html>"""
    return HTMLResponse(content=html)


@router.get("/payment/cancel", response_class=HTMLResponse)
async def payment_cancel(request: Request):
    """Shown when the user cancels / goes back from the Moyasar payment page."""
    html = """<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>شواهد AI — إلغاء الدفع</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Segoe UI', Tahoma, Arial, sans-serif;
      background: #f8fafc; color: #1e293b;
      min-height: 100vh; display: flex;
      align-items: center; justify-content: center; padding: 24px;
    }
    .card {
      background: #fff; border-radius: 16px;
      box-shadow: 0 4px 24px rgba(0,0,0,.08);
      padding: 48px 40px; max-width: 480px; width: 100%; text-align: center;
    }
    .icon { font-size: 64px; margin-bottom: 24px; }
    h1 { font-size: 1.6rem; margin-bottom: 12px; color: #0f172a; }
    p { font-size: 1rem; color: #475569; line-height: 1.7; margin-bottom: 16px; }
    .wa-btn {
      display: inline-block; margin-top: 24px; background: #25d366;
      color: #fff; padding: 12px 28px; border-radius: 8px;
      text-decoration: none; font-size: 1rem; font-weight: 600;
    }
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">↩️</div>
    <h1>لم يتم إتمام الدفع</h1>
    <p>يبدو أنك عدت قبل إتمام الدفع. لا بأس — يمكنك المحاولة مجدداً في أي وقت.</p>
    <p style="font-size:.9rem;color:#94a3b8;">
      لإنشاء ملف الشواهد PDF، أرسل كلمة <strong>تصدير</strong> في واتساب وستصلك رابط الدفع مجدداً.
    </p>
    <a href="https://wa.me/" class="wa-btn">العودة إلى واتساب</a>
  </div>
</body>
</html>"""
    return HTMLResponse(content=html)
