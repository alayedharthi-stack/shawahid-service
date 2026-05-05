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
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.services.teachers import get_or_create_teacher, get_teacher_by_id
from app.services.evidences import create_evidence, get_teacher_evidences
from app.services.storage import download_and_save, detect_evidence_type
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
)
from app.services import moyasar as moyasar_svc
from app.services.gpt_brain import ask_gpt, build_teacher_context
from app.services.whatsapp import (
    get_meta_media_url,
    send_whatsapp_message,
    send_whatsapp_button,
    build_payment_receipt_message,
    build_subscription_activated_message,
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

    from_phone: str = normalize_phone(parsed["from_phone"])
    text: str | None = parsed.get("text")
    media_id: str | None = parsed.get("media_id")
    mime_type: str | None = parsed.get("mime_type")
    file_name: str | None = parsed.get("file_name")
    media_url: str | None = parsed.get("media_url")

    # Resolve temporary Meta media URL
    if media_id and not media_url:
        media_url = await get_meta_media_url(media_id)

    teacher = get_or_create_teacher(db, from_phone)
    evidence_type = detect_evidence_type(mime_type, file_name, text)

    logger.info(
        "[WA INBOUND] teacher_id=%d type=%s has_media=%s text=%r",
        teacher.id, evidence_type, bool(media_id), (text or "")[:60],
    )

    # ── Load profile + subscription (backend-only decision) ──────────────────
    from app.services.teachers import update_teacher

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
        school_name=teacher.school_name,
        evidence_count=evidence_count,
    )
    logger.info(
        "[USER PROFILE LOADED] teacher_id=%d name=%r evidence_count=%d",
        teacher.id, teacher.name, evidence_count,
    )

    # ── Download images for GPT Vision (base64 only, never pass WA URLs) ─────
    # WhatsApp media URLs are auth-protected — GPT cannot access them directly.
    storage_path: str | None = None
    safe_filename: str | None = None
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

    # ── Ask GPT — GPT is the brain, writes every reply ───────────────────────
    decision = await ask_gpt(
        text=text,
        teacher_context=teacher_context,
        storage_path=storage_path if evidence_type == "image" else None,
        image_url=None,   # NEVER pass WA URLs — they require auth GPT can't provide
        mime_type=mime_type,
        file_name=safe_filename or file_name,
    )

    logger.info(
        "[GPT DECISION] teacher_id=%d intent=%s should_save=%s confidence=%.2f title=%r",
        teacher.id, decision["intent"], decision["should_save"],
        decision["confidence"], decision["title"],
    )

    intent = decision["intent"]
    reply  = decision["reply"]

    # ── Intent routing ────────────────────────────────────────────────────────

    if intent == "update_profile":
        # GPT detected name/profile info → save to teacher record, never as evidence
        profile_data = decision.get("profile_update") or {}
        clean = {k: v for k, v in profile_data.items() if v}
        if clean:
            update_teacher(db, teacher, clean)
            logger.info(
                "[PROFILE UPDATED] teacher_id=%d fields=%s",
                teacher.id, list(clean.keys()),
            )
        # GPT already wrote the reply (e.g. "تم حفظ اسمك يا تركي 🌿")

    elif intent in ("my_files", "my_data", "edit_data", "smalltalk", "help"):
        # GPT has all context (evidence count, teacher profile) and writes the reply.
        # Backend does nothing extra — GPT's reply is used directly.
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

            display_name = teacher.name or ""
            greeting     = f"يا {display_name}، " if display_name else ""
            body_text    = (
                f"{greeting}تصدير ملف PDF متاح بعد تفعيل الاشتراك 🌿\n"
                f"يمكنك السداد من الرابط:"
            )

            # Interactive CTA button — not a plain text URL
            background_tasks.add_task(
                send_whatsapp_button,
                teacher.phone,
                body_text,
                "سداد الاشتراك",
                payment_url,
                teacher_id=teacher.id,
            )
            return {
                "ok": False,
                "teacher_id": teacher.id,
                "intent": intent,
                "reason": f"subscription_{sub_info['status']}",
                "payment_url": payment_url,
            }

        # active_paid → allow export
        logger.info("[EXPORT ALLOWED] teacher_id=%d evidence_count=%d", teacher.id, evidence_count)
        export_record = exporter_svc.create_export_record(db, teacher.id)
        background_tasks.add_task(
            exporter_svc.run_export_background,
            teacher_id=teacher.id,
            export_id=export_record.id,
        )
        # Natural, friendly message — GPT will send the PDF URL when done
        teacher_name = teacher.name or "أستاذ"
        reply = f"يا {teacher_name}، جارٍ إنشاء ملف شواهدك الآن ⏳ سيصلك رابط التحميل فور الانتهاء."

    elif decision["should_save"]:
        # GPT decided this is evidence worth saving
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
            category=decision["category"],
            title=decision["title"],
            ai_status="completed",
            ai_raw=dict(decision),
        )
        logger.info(
            "[EVIDENCE SAVED] teacher_id=%d evidence_id=%d category=%r confidence=%.2f",
            teacher.id, evidence.id, decision["category"], decision["confidence"],
        )

    elif intent == "failure":
        # GPT failed after all retries.
        # Send interim message + schedule background retry (without re-sending failure msg).
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
        return {"ok": False, "teacher_id": teacher.id, "intent": "failure", "retrying": True}

    else:
        logger.info("[EVIDENCE SKIPPED] teacher_id=%d intent=%s", teacher.id, intent)

    # ── Send reply ────────────────────────────────────────────────────────────
    background_tasks.add_task(
        send_whatsapp_message, teacher.phone, reply, teacher_id=teacher.id
    )
    return {"ok": True, "teacher_id": teacher.id, "intent": intent, "reply": reply}


# ─── Moyasar Payment Webhook (POST) ──────────────────────────────────────────

async def _process_moyasar_payment(
    payment_id: str,
    status: str,
    amount_halalah: int,
    service: str,
    teacher_id: int,
    plan_slug: str,
    raw_data: dict,
) -> None:
    """
    Background task: validate → activate subscription → send receipt via WhatsApp.
    Opens its own DB session to avoid DetachedInstanceError.
    Always returns (never raises) so the webhook caller gets 200 fast.

    Notification strategy (no email, no Moyasar notifications, no WA templates):
      1. Build a full text receipt with invoice details.
      2. Send via session WhatsApp message.
      3. If send fails (e.g. 24h window expired) → log [PAYMENT WHATSAPP SEND FAILED].
    """
    from datetime import datetime, timezone
    from app.db.base import SessionLocal

    db = SessionLocal()
    try:
        # ── Update payment attempt record ─────────────────────────────────────
        update_payment_attempt_status(db, payment_id, status, raw_data)

        # ── Guard 1: service must be "shawahid" ───────────────────────────────
        if service and service != moyasar_svc.SHAWAHID_SERVICE_ID:
            logger.warning(
                "Moyasar webhook: service='%s' is not '%s' — skipping activation "
                "| payment_id=%s teacher_id=%d",
                service, moyasar_svc.SHAWAHID_SERVICE_ID, payment_id, teacher_id,
            )
            return

        # ── Guard 2: status ───────────────────────────────────────────────────
        if status != "paid":
            logger.info("Moyasar payment %s status=%s — no action", payment_id, status)
            return

        # ── Guard 3: amount ───────────────────────────────────────────────────
        if amount_halalah < settings.SHAWAHID_LAUNCH_PRICE_HALALAH:
            logger.warning(
                "Moyasar payment %s amount=%d halalah < required %d — skipping",
                payment_id, amount_halalah, settings.SHAWAHID_LAUNCH_PRICE_HALALAH,
            )
            return

        # ── Guard 4: teacher exists ───────────────────────────────────────────
        teacher = get_teacher_by_id(db, teacher_id)
        if not teacher:
            logger.error("Moyasar webhook: teacher_id=%d not found", teacher_id)
            return

        # ── Activate subscription ─────────────────────────────────────────────
        paid_at = datetime.now(timezone.utc)
        activate_subscription(
            db=db,
            teacher_id=teacher_id,
            payment_provider="moyasar",
            payment_reference=payment_id,
            amount_sar=float(amount_halalah) / 100,
            plan_slug=plan_slug,
        )
        logger.info(
            "[PAYMENT ACTIVATED] teacher_id=%d payment_id=%s service=%s",
            teacher_id, payment_id, service,
        )

        # ── Build receipt ─────────────────────────────────────────────────────
        # Use name from DB; fall back to "معلم رقم {phone}"
        from app.services.moyasar import _teacher_display_name
        display_name = _teacher_display_name(teacher.name or "", teacher.phone or "")

        receipt_msg = build_payment_receipt_message(
            teacher_display_name=display_name,
            teacher_phone=teacher.phone or "",
            provider_payment_id=payment_id,
            paid_at=paid_at,
            amount_sar=settings.SHAWAHID_LAUNCH_PRICE_SAR,
            plan_slug=plan_slug,
        )

        # ── Send receipt via WhatsApp (session message — no template needed) ──
        sent = await send_whatsapp_message(
            teacher.phone,
            receipt_msg,
            teacher_id=teacher_id,
            context="payment_receipt",
        )

        if sent:
            logger.info(
                "[PAYMENT RECEIPT SENT] teacher_id=%d provider_payment_id=%s",
                teacher_id, payment_id,
            )
        else:
            logger.warning(
                "[PAYMENT WHATSAPP SEND FAILED] teacher_id=%d "
                "provider_payment_id=%s reason=see_above",
                teacher_id, payment_id,
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
        service=parsed["service"],
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
