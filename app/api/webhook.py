"""
WhatsApp Cloud API webhooks + Moyasar payment webhook + payment success page.

GET  /webhook/whatsapp              — Meta webhook verification challenge
POST /webhook/whatsapp              — Incoming WhatsApp messages
POST /webhook/payment               — Moyasar payment webhook (activates subscription)
GET  /payment/success               — Redirect page after Moyasar checkout
"""
import logging
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.services.teachers import get_or_create_teacher, get_teacher_by_id
from app.services.evidences import create_evidence, get_teacher_evidences
from app.services.storage import download_and_save, detect_evidence_type
from app.services.subscriptions import (
    is_subscription_active,
    activate_subscription,
    get_payment_link,
    LAUNCH_AMOUNT_SAR,
)
from app.services.payments import (
    create_payment_attempt,
    update_payment_attempt_status,
)
from app.services import moyasar as moyasar_svc
from app.services.classifier import classify_evidence
from app.services.whatsapp import (
    detect_command,
    get_meta_media_url,
    send_whatsapp_message,
    build_my_files_reply,
    build_my_data_reply,
    build_edit_data_template,
    build_subscription_required_reply,
    build_payment_link_message,
    build_subscription_activated_message,
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


# ─── Moyasar helper — create invoice and save attempt ───────────────────────

async def _create_moyasar_link(db: Session, teacher_id: int, teacher_name: str = "") -> str:
    """
    Creates a Moyasar invoice, saves a payment_attempt record, and returns
    the payment URL.  Falls back to the static PAYMENT_LINK_TEMPLATE if
    Moyasar is not configured (dev / testing).
    """
    if not settings.MOYASAR_SECRET_KEY:
        logger.warning("MOYASAR_SECRET_KEY not set — using fallback payment link")
        return get_payment_link(teacher_id)

    result = await moyasar_svc.create_invoice(
        teacher_id=teacher_id,
        teacher_name=teacher_name,
    )
    create_payment_attempt(
        db=db,
        teacher_id=teacher_id,
        provider_payment_id=result["provider_payment_id"],
        payment_url=result["payment_url"],
        raw_response=result["raw_response"],
    )
    return result["payment_url"]


# ─── Main Webhook (POST) ─────────────────────────────────────────────────────

@router.post("/webhook/whatsapp")
async def whatsapp_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    parsed = _parse_meta_payload(body)
    if parsed is None:
        parsed = _parse_simple_payload(body)
    if parsed is None:
        return {"ok": True, "skipped": True}

    from_phone: str = normalize_phone(parsed["from_phone"])
    text: str | None = parsed.get("text")
    media_id: str | None = parsed.get("media_id")
    mime_type: str | None = parsed.get("mime_type")
    file_name: str | None = parsed.get("file_name")
    media_url: str | None = parsed.get("media_url")

    if media_id and not media_url:
        media_url = await get_meta_media_url(media_id)
        if not media_url:
            logger.warning(
                "Could not fetch media URL for media_id=%s — saving text only", media_id
            )

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
            try:
                payment_url = await _create_moyasar_link(
                    db, teacher.id, teacher.name or ""
                )
            except Exception as exc:
                logger.error("Moyasar invoice creation failed: %s", exc)
                payment_url = get_payment_link(teacher.id)

            reply = build_payment_link_message(payment_url, teacher.name or "")
            background_tasks.add_task(send_whatsapp_message, teacher.phone, reply)
            return {
                "ok": False,
                "teacher_id": teacher.id,
                "reply": reply,
                "reason": "subscription_required",
                "payment_url": payment_url,
            }

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

    reply = build_evidence_saved_reply()
    background_tasks.add_task(send_whatsapp_message, teacher.phone, reply)

    return {
        "ok": True,
        "teacher_id": teacher.id,
        "evidence_id": evidence.id,
        "message": reply,
    }


# ─── Moyasar Payment Webhook (POST) ──────────────────────────────────────────

async def _process_moyasar_payment(
    payment_id: str,
    status: str,
    amount_halalah: int,
    teacher_id: int,
    plan_slug: str,
    raw_data: dict,
) -> None:
    """
    Background task: activate subscription or mark failed, then notify teacher.
    Opens its own DB session to avoid DetachedInstanceError.
    Always returns — never raises — to ensure webhook caller gets 200 fast.
    """
    from app.db.base import SessionLocal

    db = SessionLocal()
    try:
        # Update payment attempt record
        update_payment_attempt_status(db, payment_id, status, raw_data)

        if status != "paid":
            logger.info("Moyasar payment %s status=%s — no action", payment_id, status)
            return

        if amount_halalah < settings.SHAWAHID_LAUNCH_PRICE_HALALAH:
            logger.warning(
                "Moyasar payment %s amount=%d halalah < required %d — skipping",
                payment_id, amount_halalah, settings.SHAWAHID_LAUNCH_PRICE_HALALAH,
            )
            return

        teacher = get_teacher_by_id(db, teacher_id)
        if not teacher:
            logger.error("Moyasar webhook: teacher_id=%d not found", teacher_id)
            return

        activate_subscription(
            db=db,
            teacher_id=teacher_id,
            payment_provider="moyasar",
            payment_reference=payment_id,
            amount_sar=float(amount_halalah) / 100,
            plan_slug=plan_slug,
        )
        logger.info(
            "Subscription activated: teacher_id=%d payment_id=%s", teacher_id, payment_id
        )

        # Notify teacher via WhatsApp
        try:
            await send_whatsapp_message(
                teacher.phone,
                build_subscription_activated_message(),
            )
        except Exception as exc:
            logger.error(
                "WhatsApp notification failed for teacher %d: %s", teacher_id, exc
            )

    except Exception as exc:
        logger.error("Error processing Moyasar payment %s: %s", payment_id, exc)
    finally:
        db.close()


@router.post("/webhook/payment")
async def payment_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
):
    """
    Moyasar webhook endpoint.
    Verifies signature, parses payload, schedules background processing.
    Always returns 200 immediately.
    """
    raw_body = await request.body()

    # Verify Moyasar signature
    signature = request.headers.get("Moyasar-Signature", "")
    if not moyasar_svc.verify_webhook_signature(raw_body, signature):
        logger.warning("Moyasar webhook signature verification FAILED")
        raise HTTPException(status_code=403, detail="Invalid signature")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    parsed = moyasar_svc.parse_webhook_payload(payload)
    if parsed is None:
        return {"ok": True, "skipped": True}

    background_tasks.add_task(
        _process_moyasar_payment,
        payment_id=parsed["payment_id"],
        status=parsed["status"],
        amount_halalah=parsed["amount_halalah"],
        teacher_id=parsed["teacher_id"],
        plan_slug=parsed["plan_slug"],
        raw_data=parsed["raw"],
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
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">✅</div>
    <h1>تم استلام عملية الدفع</h1>
    <p>
      إذا اكتملت عملية الدفع بنجاح، سيتم تفعيل اشتراكك في شواهد AI تلقائيًا خلال لحظات.
    </p>
    <div class="highlight">
      يمكنك العودة إلى واتساب وكتابة:<br>
      <strong style="font-size:1.1rem;letter-spacing:.05em;">تصدير</strong><br>
      لإنشاء ملف الشواهد PDF فور تفعيل الاشتراك.
    </div>
    <a href="https://wa.me/" class="wa-btn">العودة إلى واتساب</a>
  </div>
</body>
</html>"""
    return HTMLResponse(content=html)
