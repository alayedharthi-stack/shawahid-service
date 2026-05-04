"""
WhatsApp Cloud API webhooks + payment webhook.

GET  /webhook/whatsapp  — Meta webhook verification challenge
POST /webhook/whatsapp  — Incoming messages (Meta Cloud API format OR simple test format)
POST /webhook/payment   — Payment gateway callback to activate subscriptions
"""
import logging
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.schemas.subscription import PaymentWebhookIn
from app.services.teachers import get_or_create_teacher
from app.services.evidences import create_evidence, get_teacher_evidences
from app.services.storage import download_and_save, detect_evidence_type
from app.services.subscriptions import (
    is_subscription_active,
    activate_subscription,
    get_payment_link,
    LAUNCH_AMOUNT_SAR,
)
from app.services.classifier import classify_evidence
from app.services.whatsapp import (
    detect_command,
    get_meta_media_url,
    send_whatsapp_message,
    build_my_files_reply,
    build_my_data_reply,
    build_edit_data_template,
    build_subscription_required_reply,
    build_evidence_saved_reply,
)
from app.services import exporter as exporter_svc
from app.core.config import settings
from app.core.phone import normalize_phone

router = APIRouter()
logger = logging.getLogger(__name__)


# ─── Meta Webhook Verification (GET) ─────────────────────────────────────────

@router.get("/webhook/whatsapp")
async def verify_webhook(request: Request):
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == "shawahid_verify_2026":
        return int(challenge)

    return {"error": "verification failed"}


# ─── Payload parsing helpers ─────────────────────────────────────────────────

def _parse_meta_payload(body: dict) -> dict | None:
    """
    Parse a real Meta Cloud API webhook payload.
    Returns a normalised dict or None if the body is not Meta format
    or contains no actionable message (e.g. delivery receipts).
    """
    try:
        if body.get("object") != "whatsapp_business_account":
            return None
        entry = body["entry"][0]
        change = entry["changes"][0]
        value = change["value"]
        messages = value.get("messages")
        if not messages:
            return None  # status update only — ignore

        msg = messages[0]
        from_phone = msg["from"]
        msg_type = msg.get("type", "text")

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

        else:
            logger.info("Unsupported Meta message type: %s — ignoring", msg_type)
            return None

        return {
            "from_phone": from_phone,
            "text": text,
            "media_id": media_id,
            "mime_type": mime_type,
            "file_name": file_name,
        }
    except (KeyError, IndexError, TypeError) as exc:
        logger.debug("Meta payload parse error: %s", exc)
        return None


def _parse_simple_payload(body: dict) -> dict | None:
    """Parse the simple test payload used in dev/curl testing."""
    if "from_phone" not in body:
        return None
    return {
        "from_phone": body["from_phone"],
        "text": body.get("text"),
        "media_id": body.get("media_id"),
        "mime_type": body.get("mime_type"),
        "file_name": body.get("file_name"),
        "media_url": body.get("media_url"),
    }


# ─── Main Webhook (POST) ─────────────────────────────────────────────────────

@router.post("/webhook/whatsapp")
async def whatsapp_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Handles incoming WhatsApp messages.
    Supports both:
    - Meta Cloud API format (real production payload)
    - Simple JSON format (dev/testing: {"from_phone": "...", "text": "..."})
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Try Meta format first, fall back to simple test format
    parsed = _parse_meta_payload(body)
    if parsed is None:
        parsed = _parse_simple_payload(body)
    if parsed is None:
        # Could be a Meta status update (delivery receipt) — return 200 to ack
        logger.debug("Unrecognised webhook payload — returning 200 to ack")
        return {"ok": True, "skipped": True}

    from_phone: str = normalize_phone(parsed["from_phone"])
    text: str | None = parsed.get("text")
    media_id: str | None = parsed.get("media_id")
    mime_type: str | None = parsed.get("mime_type")
    file_name: str | None = parsed.get("file_name")
    # Simple test payloads may already include a direct media_url
    media_url: str | None = parsed.get("media_url")

    # If we have a Meta media_id, resolve the temporary download URL
    if media_id and not media_url:
        media_url = await get_meta_media_url(media_id)
        if not media_url:
            logger.warning("Could not fetch media URL for media_id=%s — saving text only", media_id)

    teacher = get_or_create_teacher(db, from_phone)
    command = detect_command(text)

    # ── Commands ─────────────────────────────────────────────────────────────
    if command == "my_files":
        evidences = get_teacher_evidences(db, teacher.id)
        sub_active = is_subscription_active(db, teacher.id)
        reply = build_my_files_reply(len(evidences), sub_active)
        background_tasks.add_task(send_whatsapp_message, teacher.phone, reply)
        return {"ok": True, "teacher_id": teacher.id, "reply": reply}

    if command == "my_data":
        reply = build_my_data_reply(teacher)
        background_tasks.add_task(send_whatsapp_message, teacher.phone, reply)
        return {"ok": True, "teacher_id": teacher.id, "reply": reply}

    if command == "edit_data":
        reply = build_edit_data_template()
        background_tasks.add_task(send_whatsapp_message, teacher.phone, reply)
        return {"ok": True, "teacher_id": teacher.id, "reply": reply}

    if command == "export":
        if not is_subscription_active(db, teacher.id):
            link = get_payment_link(teacher.id)
            reply = build_subscription_required_reply(link)
            background_tasks.add_task(send_whatsapp_message, teacher.phone, reply)
            return {"ok": False, "teacher_id": teacher.id, "reply": reply, "reason": "subscription_required"}

        export_record = exporter_svc.create_export_record(db, teacher.id)
        background_tasks.add_task(
            exporter_svc.run_export_background,
            teacher_id=teacher.id,
            export_id=export_record.id,
        )
        reply = "جارٍ إنشاء ملف الشواهد... سيصلك رابط التحميل قريبًا. ⏳"
        background_tasks.add_task(send_whatsapp_message, teacher.phone, reply)
        return {
            "ok": True,
            "teacher_id": teacher.id,
            "export_id": export_record.id,
            "reply": reply,
        }

    # ── Save evidence ─────────────────────────────────────────────────────────
    storage_path: str | None = None
    safe_filename: str | None = None
    evidence_type = detect_evidence_type(mime_type, file_name, text)

    if media_url:
        try:
            storage_path, safe_filename = await download_and_save(
                teacher_id=teacher.id,
                media_url=media_url,
                original_filename=file_name,
                mime_type=mime_type,
                # Pass token so Meta CDN URLs that require auth are downloaded correctly
                auth_token=settings.WHATSAPP_ACCESS_TOKEN or None,
            )
        except Exception as exc:
            logger.error("Media download failed for teacher %d: %s", teacher.id, exc)

    evidence = create_evidence(
        db=db,
        teacher_id=teacher.id,
        source_phone=teacher.phone,
        evidence_type=evidence_type,
        message_text=text,
        media_url=media_url,
        storage_path=storage_path,
        file_name=safe_filename or file_name,
        mime_type=mime_type,
    )

    background_tasks.add_task(
        classify_evidence,
        evidence_id=evidence.id,
        message_text=text,
        image_url=media_url if evidence_type == "image" else None,
        evidence_type=evidence_type,
    )

    # Acknowledge receipt via WhatsApp
    reply = build_evidence_saved_reply()
    background_tasks.add_task(send_whatsapp_message, teacher.phone, reply)

    return {
        "ok": True,
        "teacher_id": teacher.id,
        "evidence_id": evidence.id,
        "message": reply,
    }


# ─── Payment Webhook (POST) ───────────────────────────────────────────────────

@router.post("/webhook/payment")
async def payment_webhook(payload: PaymentWebhookIn, db: Session = Depends(get_db)):
    """
    Payment gateway callback. Activates subscription on successful payment.
    Expects amount_sar >= LAUNCH_AMOUNT_SAR (29 SAR) to activate.
    """
    amount = payload.amount_sar or LAUNCH_AMOUNT_SAR

    if amount < LAUNCH_AMOUNT_SAR:
        raise HTTPException(
            status_code=400,
            detail=f"المبلغ {amount} ريال أقل من الحد الأدنى للاشتراك ({LAUNCH_AMOUNT_SAR} ريال)",
        )

    sub = activate_subscription(
        db=db,
        teacher_id=payload.teacher_id,
        payment_provider=payload.payment_provider,
        payment_reference=payload.payment_reference,
        amount_sar=amount,
    )
    return {
        "ok": True,
        "teacher_id": payload.teacher_id,
        "subscription_id": sub.id,
        "status": sub.status,
        "plan_slug": sub.plan_slug,
        "ends_at": sub.ends_at.isoformat() if sub.ends_at else None,
    }
