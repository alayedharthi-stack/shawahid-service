import logging
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.schemas.evidence import WhatsAppWebhookIn
from app.schemas.subscription import PaymentWebhookIn
from app.services.teachers import get_or_create_teacher
from app.services.evidences import create_evidence, get_teacher_evidences
from app.services.storage import download_and_save, detect_evidence_type
from app.services.subscriptions import is_subscription_active, activate_subscription, get_payment_link
from app.services.classifier import classify_evidence
from app.services.whatsapp import (
    detect_command,
    build_my_files_reply,
    build_my_data_reply,
    build_edit_data_template,
    build_subscription_required_reply,
)
from app.services import exporter as exporter_svc
from app.core.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/webhook/whatsapp")
async def whatsapp_webhook(payload: WhatsAppWebhookIn, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    teacher = get_or_create_teacher(db, payload.from_phone)
    command = detect_command(payload.text)

    # ── Handle known commands ──────────────────────────────────────────────
    if command == "my_files":
        evidences = get_teacher_evidences(db, teacher.id)
        reply = build_my_files_reply(len(evidences))
        return {"ok": True, "teacher_id": teacher.id, "reply": reply}

    if command == "my_data":
        reply = build_my_data_reply(teacher)
        return {"ok": True, "teacher_id": teacher.id, "reply": reply}

    if command == "edit_data":
        reply = build_edit_data_template()
        return {"ok": True, "teacher_id": teacher.id, "reply": reply}

    if command == "export":
        if not is_subscription_active(db, teacher.id):
            link = get_payment_link(teacher.id)
            reply = build_subscription_required_reply(link)
            return {"ok": False, "teacher_id": teacher.id, "reply": reply, "reason": "subscription_required"}

        export_record = exporter_svc.create_export_record(db, teacher.id)
        background_tasks.add_task(
            exporter_svc.run_export_background,
            db=db,
            teacher=teacher,
            export_id=export_record.id,
        )
        return {
            "ok": True,
            "teacher_id": teacher.id,
            "export_id": export_record.id,
            "reply": "جارٍ إنشاء ملف الشواهد... سيصلك رابط التحميل قريبًا.",
        }

    # ── Save as evidence ───────────────────────────────────────────────────
    storage_path = None
    safe_filename = None
    evidence_type = detect_evidence_type(payload.mime_type, payload.file_name, payload.text)

    if payload.media_url:
        try:
            storage_path, safe_filename = await download_and_save(
                teacher_id=teacher.id,
                media_url=payload.media_url,
                original_filename=payload.file_name,
                mime_type=payload.mime_type,
            )
        except Exception as exc:
            logger.error("Media download failed for teacher %d: %s", teacher.id, exc)

    evidence = create_evidence(
        db=db,
        teacher_id=teacher.id,
        source_phone=teacher.phone,
        evidence_type=evidence_type,
        message_text=payload.text,
        media_url=payload.media_url,
        storage_path=storage_path,
        file_name=safe_filename or payload.file_name,
        mime_type=payload.mime_type,
    )

    background_tasks.add_task(
        classify_evidence,
        db=db,
        evidence_id=evidence.id,
        message_text=payload.text,
        image_url=payload.media_url if evidence_type == "image" else None,
        evidence_type=evidence_type,
    )

    return {
        "ok": True,
        "teacher_id": teacher.id,
        "evidence_id": evidence.id,
        "message": "تم حفظ الشاهد بنجاح",
    }


@router.post("/webhook/payment")
async def payment_webhook(payload: PaymentWebhookIn, db: Session = Depends(get_db)):
    """Generic payment webhook. Activate subscription on successful payment."""
    sub = activate_subscription(
        db=db,
        teacher_id=payload.teacher_id,
        payment_provider=payload.payment_provider,
        payment_reference=payload.payment_reference,
        amount_sar=payload.amount_sar or 49.00,
    )
    return {
        "ok": True,
        "teacher_id": payload.teacher_id,
        "subscription_id": sub.id,
        "status": sub.status,
        "ends_at": sub.ends_at.isoformat() if sub.ends_at else None,
    }
