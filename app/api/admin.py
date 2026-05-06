"""
Admin panel routes — protected by Basic Auth (require_admin dependency).
All data access is teacher-scoped; no endpoint exposes cross-teacher data without admin auth.
"""
import asyncio
import logging
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.security import require_admin
from app.db.base import get_db
from app.models.teacher import Teacher
from app.models.evidence import Evidence
from app.models.portfolio_export import PortfolioExport
from app.models.subscription import TeacherSubscription
from app.services.evidences import (
    ALLOWED_CATEGORIES,
    get_evidence_by_id,
    get_teacher_evidences,
    update_evidence,
    delete_evidence,
)
from app.services.subscriptions import (
    get_subscription_status,
    activate_subscription,
    get_payment_link,
    list_subscriptions,
    LAUNCH_AMOUNT_SAR,
)
from app.services.payments import (
    create_payment_attempt,
    get_latest_payment_attempt,
    list_payment_attempts,
)
from app.services import moyasar as moyasar_svc
from app.services.teachers import get_teacher_by_id
from app.services.whatsapp import (
    send_whatsapp_message,
    build_subscription_required_reply,
    build_payment_link_message,
)
from app.services import exporter as exporter_svc
from app.services.followups import run_inactive_user_followups
from app.core.config import settings

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger(__name__)


def _sub_status_for_teacher(db: Session, teacher: Teacher) -> str:
    """Uses the unified get_subscription_status() — single source of truth."""
    return get_subscription_status(db, teacher.id)["status"]


def _active_subscription_for_teacher(db: Session, teacher_id: int) -> TeacherSubscription | None:
    now = datetime.utcnow()
    return (
        db.query(TeacherSubscription)
        .filter(
            TeacherSubscription.teacher_id == teacher_id,
            TeacherSubscription.status == "active",
            TeacherSubscription.ends_at.isnot(None),
            TeacherSubscription.ends_at > now,
        )
        .order_by(TeacherSubscription.ends_at.desc())
        .first()
    )


def _sub_ends_at(teacher: Teacher) -> str | None:
    now = datetime.utcnow()
    active = next(
        (s for s in teacher.subscriptions
         if s.status == "active" and s.ends_at
         and s.ends_at.replace(tzinfo=None) > now),
        None,
    )
    if active and active.ends_at:
        return active.ends_at.strftime("%Y/%m/%d")
    return None


# ─── Dashboard ─────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
def admin_dashboard(request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin)):
    total_teachers = db.query(func.count(Teacher.id)).scalar()
    total_evidences = db.query(func.count(Evidence.id)).scalar()
    total_exports = db.query(func.count(PortfolioExport.id)).filter(PortfolioExport.status == "done").scalar()
    now = datetime.utcnow()
    active_subs = (
        db.query(func.count(TeacherSubscription.id))
        .filter(TeacherSubscription.status == "active", TeacherSubscription.ends_at > now)
        .scalar()
    )

    recent_teachers = db.query(Teacher).order_by(Teacher.id.desc()).limit(10).all()
    evidence_counts = {
        row[0]: row[1]
        for row in db.query(Evidence.teacher_id, func.count(Evidence.id)).group_by(Evidence.teacher_id).all()
    }

    enriched = []
    for t in recent_teachers:
        enriched.append({
            "id": t.id, "name": t.name, "phone": t.phone, "school_name": t.school_name,
            "sub_status": _sub_status_for_teacher(db, t),
            "evidence_count": evidence_counts.get(t.id, 0),
        })

    return templates.TemplateResponse("admin/dashboard.html", {
        "request": request,
        "active": "dashboard",
        "total_teachers": total_teachers,
        "total_evidences": total_evidences,
        "total_exports": total_exports,
        "active_subs": active_subs,
        "recent_teachers": enriched,
    })


@router.post("/followups/run")
async def admin_run_followups(
    dry_run: bool = False,
    limit: int = 100,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    """Run segmented inactive-user follow-ups once.

    Intended for manual admin use or an external cron. The service itself still
    enforces one follow-up per teacher via `Teacher.followup_sent_at`.
    """
    limit = max(1, min(limit, 500))
    return await run_inactive_user_followups(db, limit=limit, dry_run=dry_run)


# ─── Teachers list ──────────────────────────────────────────────────────────

@router.get("/teachers", response_class=HTMLResponse)
def admin_teachers(request: Request, q: str = "", db: Session = Depends(get_db), _: str = Depends(require_admin)):
    query = db.query(Teacher)
    if q:
        query = query.filter(
            Teacher.name.ilike(f"%{q}%") | Teacher.phone.ilike(f"%{q}%")
        )
    teachers_raw = query.order_by(Teacher.id.desc()).all()

    evidence_counts = {
        row[0]: row[1]
        for row in db.query(Evidence.teacher_id, func.count(Evidence.id)).group_by(Evidence.teacher_id).all()
    }

    teachers = []
    for t in teachers_raw:
        active_sub = _active_subscription_for_teacher(db, t.id)
        sub_status = "active" if active_sub else _sub_status_for_teacher(db, t)
        teachers.append({
            "id": t.id, "name": t.name, "phone": t.phone, "school_name": t.school_name,
            "subject": t.subject,
            "sub_status": sub_status,
            "sub_ends_at": active_sub.ends_at.strftime("%Y/%m/%d") if active_sub and active_sub.ends_at else _sub_ends_at(t),
            "sub_amount_sar": active_sub.amount_sar if active_sub else None,
            "evidence_count": evidence_counts.get(t.id, 0),
        })

    return templates.TemplateResponse("admin/teachers.html", {
        "request": request, "active": "teachers",
        "teachers": teachers, "total": len(teachers), "q": q,
    })


# ─── Teacher detail ─────────────────────────────────────────────────────────

@router.get("/teachers/{teacher_id}", response_class=HTMLResponse)
def admin_teacher_detail(
    teacher_id: int, request: Request,
    db: Session = Depends(get_db), _: str = Depends(require_admin),
):
    teacher = get_teacher_by_id(db, teacher_id)
    if not teacher:
        raise HTTPException(status_code=404, detail="المعلم غير موجود")

    evidences = get_teacher_evidences(db, teacher_id, limit=500)
    sub = next(iter(teacher.subscriptions), None) if teacher.subscriptions else None
    payment_attempts = list_payment_attempts(db, teacher_id, limit=5)

    flash_success = request.query_params.get("success")
    flash_error = request.query_params.get("error")

    sub_status = get_subscription_status(db, teacher_id)

    return templates.TemplateResponse("admin/teacher_detail.html", {
        "request": request, "active": "teachers",
        "teacher": teacher, "evidences": evidences,
        "subscription": sub,
        "sub_status": sub_status["status"],       # "active_paid" | "pending" | "expired" | "unpaid"
        "sub_verified_payment": sub_status.get("payment"),
        "payment_attempts": payment_attempts,
        "trust_info": settings.trust_info,
        "flash_success": flash_success, "flash_error": flash_error,
    })


# ─── Manual subscription activation ─────────────────────────────────────────

@router.post("/teachers/{teacher_id}/activate-subscription")
def admin_activate_subscription(
    teacher_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    """
    Manually activate a 29 SAR launch subscription for testing/manual payment scenarios.
    Sets plan_slug=launch_annual_29, payment_provider=manual, ends_at=now+365d.
    """
    teacher = get_teacher_by_id(db, teacher_id)
    if not teacher:
        raise HTTPException(status_code=404, detail="المعلم غير موجود")

    sub = activate_subscription(
        db=db,
        teacher_id=teacher_id,
        payment_provider="manual",
        payment_reference=f"admin-manual-{teacher_id}",
        amount_sar=29.00,
        plan_slug="launch_annual_29",
    )
    return RedirectResponse(
        url=f"/admin/teachers/{teacher_id}?success=تم+تفعيل+الاشتراك+يدويًا+حتى+{sub.ends_at.strftime('%Y/%m/%d') if sub.ends_at else ''}",
        status_code=303,
    )


# ─── Send payment link ───────────────────────────────────────────────────────

@router.post("/teachers/{teacher_id}/send-payment-link")
async def admin_send_payment_link(
    teacher_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    teacher = get_teacher_by_id(db, teacher_id)
    if not teacher:
        raise HTTPException(status_code=404, detail="المعلم غير موجود")

    # Try to create a real Moyasar invoice; fall back to static template link
    if settings.MOYASAR_SECRET_KEY:
        try:
            result = await moyasar_svc.create_invoice(
                service="shawahid",
                teacher_id=teacher_id,
                teacher_phone=teacher.phone or "",
                teacher_name=teacher.name or "",
            )
            payment_url = result["payment_url"]
            create_payment_attempt(
                db=db,
                teacher_id=teacher_id,
                provider_payment_id=result["provider_payment_id"],
                payment_url=payment_url,
                raw_response=result["raw_response"],
                metadata=result["metadata"],
            )
        except Exception as exc:
            logger.error("Moyasar invoice creation failed for teacher %d: %s", teacher_id, exc)
            payment_url = get_payment_link(teacher_id)
    else:
        payment_url = get_payment_link(teacher_id)

    from app.services.moyasar import _teacher_display_name as _dn
    display = _dn(teacher.name or "", teacher.phone or "")
    msg = build_payment_link_message(payment_url, display)
    await send_whatsapp_message(
        teacher.phone, msg,
        teacher_id=teacher_id, context="payment_link",
    )
    logger.info("[PAYMENT LINK SENT] teacher_id=%d channel=whatsapp (admin)", teacher_id)

    return RedirectResponse(
        url=f"/admin/teachers/{teacher_id}?success=تم+إنشاء+وإرسال+رابط+الدفع+بنجاح",
        status_code=303,
    )


# ─── Export from admin ───────────────────────────────────────────────────────

@router.post("/teachers/{teacher_id}/export")
async def admin_export(
    teacher_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    teacher = get_teacher_by_id(db, teacher_id)
    if not teacher:
        raise HTTPException(status_code=404, detail="المعلم غير موجود")

    record = exporter_svc.create_export_record(db, teacher_id)
    background_tasks.add_task(
        exporter_svc.run_export_background,
        teacher_id=teacher_id,
        export_id=record.id,
    )
    return RedirectResponse(
        url=f"/admin/teachers/{teacher_id}?success=جارٍ+إنشاء+ملف+PDF+للمعلم",
        status_code=303,
    )


# ─── Evidence edit ───────────────────────────────────────────────────────────

@router.get("/evidences/{evidence_id}/edit", response_class=HTMLResponse)
def admin_evidence_edit_form(
    evidence_id: int, request: Request,
    db: Session = Depends(get_db), _: str = Depends(require_admin),
):
    ev = get_evidence_by_id(db, evidence_id)
    if not ev:
        raise HTTPException(status_code=404, detail="الشاهد غير موجود")
    return templates.TemplateResponse("admin/evidence_edit.html", {
        "request": request, "active": "teachers",
        "evidence": ev, "categories": ALLOWED_CATEGORIES,
    })


@router.post("/evidences/{evidence_id}/edit")
async def admin_evidence_edit_submit(
    evidence_id: int, request: Request,
    db: Session = Depends(get_db), _: str = Depends(require_admin),
):
    ev = get_evidence_by_id(db, evidence_id)
    if not ev:
        raise HTTPException(status_code=404, detail="الشاهد غير موجود")
    form = await request.form()
    data = {k: v for k, v in form.items() if v}
    update_evidence(db, ev, data)
    return RedirectResponse(
        url=f"/admin/teachers/{ev.teacher_id}?success=تم+تحديث+الشاهد+بنجاح",
        status_code=303,
    )


@router.post("/evidences/{evidence_id}/reclassify")
async def admin_evidence_reclassify(
    evidence_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    """
    Trigger AI re-classification for a single evidence.
    Resets ai_status to 'pending', then runs the classifier as a background task.
    Safety: only this one evidence is sent to OpenAI.
    """
    from app.services.classifier import classify_evidence

    ev = get_evidence_by_id(db, evidence_id)
    if not ev:
        raise HTTPException(status_code=404, detail="الشاهد غير موجود")

    # Reset status so UI shows re-classification is in progress
    ev.ai_status = "pending"
    db.commit()

    background_tasks.add_task(
        classify_evidence,
        evidence_id=evidence_id,
        message_text=ev.message_text,
        image_url=ev.media_url if ev.evidence_type == "image" else None,
        evidence_type=ev.evidence_type or "text",
    )

    teacher_id = ev.teacher_id
    return RedirectResponse(
        url=f"/admin/teachers/{teacher_id}?success=جارٍ+إعادة+تصنيف+الشاهد+بالذكاء+الاصطناعي",
        status_code=303,
    )


@router.post("/evidences/{evidence_id}/delete")
def admin_evidence_delete(
    evidence_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    ev = get_evidence_by_id(db, evidence_id)
    if not ev:
        raise HTTPException(status_code=404)
    teacher_id = ev.teacher_id
    delete_evidence(db, ev)
    return RedirectResponse(
        url=f"/admin/teachers/{teacher_id}?success=تم+حذف+الشاهد+بنجاح",
        status_code=303,
    )


# ─── Portfolio JSON (for preview / external integrations) ────────────────────

@router.get("/teachers/{teacher_id}/portfolio-json")
def admin_portfolio_json(
    teacher_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    """Return the structured portfolio JSON for a teacher (admin only)."""
    from app.services.exporter import build_portfolio_json

    try:
        return build_portfolio_json(db, teacher_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# ─── Subscription status (JSON — for debugging & API consumers) ──────────────

@router.get("/teachers/{teacher_id}/subscription-status")
def admin_subscription_status(
    teacher_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    """
    Returns detailed subscription status for a teacher.
    Use this endpoint to diagnose payment / activation issues.

    Example: GET /admin/teachers/1/subscription-status
    """
    from app.models.subscription import TeacherSubscription as TS
    from app.models.payment_attempt import PaymentAttempt as PA

    teacher = get_teacher_by_id(db, teacher_id)
    if not teacher:
        raise HTTPException(status_code=404, detail="المعلم غير موجود")

    status_info = get_subscription_status(db, teacher_id)
    sub = status_info["sub"]
    payment = status_info["payment"]

    # Latest payment attempt (regardless of status)
    latest_pa = get_latest_payment_attempt(db, teacher_id)

    # All payment attempts for this teacher
    all_pas = list_payment_attempts(db, teacher_id, limit=10)

    return {
        "teacher_id": teacher_id,
        "teacher_name": teacher.name,
        "teacher_phone": teacher.phone,
        "subscription_status": status_info["status"],
        "subscription": {
            "id": sub.id if sub else None,
            "status": sub.status if sub else None,
            "plan_slug": sub.plan_slug if sub else None,
            "amount_sar": float(sub.amount_sar) if sub and sub.amount_sar else None,
            "starts_at": sub.starts_at.isoformat() if sub and sub.starts_at else None,
            "ends_at": sub.ends_at.isoformat() if sub and sub.ends_at else None,
            "payment_provider": sub.payment_provider if sub else None,
            "payment_reference": sub.payment_reference if sub else None,
        } if sub else None,
        "verified_payment": {
            "id": payment.id if payment else None,
            "provider_payment_id": payment.provider_payment_id if payment else None,
            "status": payment.status if payment else None,
            "amount_sar": float(payment.amount_sar) if payment and payment.amount_sar else None,
        } if payment else None,
        "latest_payment_attempt": {
            "id": latest_pa.id if latest_pa else None,
            "provider_payment_id": latest_pa.provider_payment_id if latest_pa else None,
            "status": latest_pa.status if latest_pa else None,
            "amount_sar": float(latest_pa.amount_sar) if latest_pa and latest_pa.amount_sar else None,
            "created_at": latest_pa.created_at.isoformat() if latest_pa and latest_pa.created_at else None,
            "updated_at": latest_pa.updated_at.isoformat() if latest_pa and latest_pa.updated_at else None,
        } if latest_pa else None,
        "all_payment_attempts": [
            {
                "id": pa.id,
                "provider_payment_id": pa.provider_payment_id,
                "status": pa.status,
                "amount_sar": float(pa.amount_sar) if pa.amount_sar else None,
                "created_at": pa.created_at.isoformat() if pa.created_at else None,
            }
            for pa in all_pas
        ],
    }


@router.post("/teachers/{teacher_id}/activate-payment")
async def admin_activate_payment(
    teacher_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    """
    Manually activate subscription for a teacher using a known payment/invoice ID.
    Use this when the Moyasar webhook was NOT received but payment was confirmed externally.

    Form fields:
        provider_payment_id : Moyasar invoice or payment ID (required)
        amount_sar          : Amount paid (default 29)
        plan_slug           : Plan slug (default launch_annual_29)
        send_whatsapp       : "1" to send WhatsApp receipt
    """
    from app.services.payments import upsert_paid_payment_attempt
    from app.services.whatsapp import send_whatsapp_message, build_payment_receipt_message
    from app.services.moyasar import _teacher_display_name
    from datetime import datetime, timezone

    teacher = get_teacher_by_id(db, teacher_id)
    if not teacher:
        raise HTTPException(status_code=404, detail="المعلم غير موجود")

    form = await request.form()
    provider_payment_id = (form.get("provider_payment_id") or "").strip()
    if not provider_payment_id:
        raise HTTPException(status_code=400, detail="provider_payment_id مطلوب")

    amount_sar = float(form.get("amount_sar") or 29.0)
    plan_slug = (form.get("plan_slug") or "launch_annual_29").strip()
    send_wa = form.get("send_whatsapp") == "1"

    # Upsert PaymentAttempt as paid
    pa = upsert_paid_payment_attempt(
        db=db,
        teacher_id=teacher_id,
        provider_payment_id=provider_payment_id,
        amount_sar=amount_sar,
    )
    logger.info(
        "[ADMIN MANUAL ACTIVATE] teacher_id=%d pa_id=%d provider_payment_id=%s",
        teacher_id, pa.id, provider_payment_id,
    )

    # Activate subscription
    sub = activate_subscription(
        db=db,
        teacher_id=teacher_id,
        payment_provider="moyasar_manual",
        payment_reference=provider_payment_id,
        amount_sar=amount_sar,
        plan_slug=plan_slug,
    )
    logger.info(
        "[ADMIN MANUAL ACTIVATE] subscription activated teacher_id=%d ends_at=%s",
        teacher_id, sub.ends_at,
    )

    # Optionally send WhatsApp receipt
    if send_wa and teacher.phone:
        display = _teacher_display_name(teacher.name or "", teacher.phone or "")
        paid_at = datetime.now(timezone.utc)
        msg = build_payment_receipt_message(
            teacher_display_name=display,
            teacher_phone=teacher.phone,
            provider_payment_id=provider_payment_id,
            paid_at=paid_at,
            amount_sar=int(amount_sar),
            plan_slug=plan_slug,
            starts_at=sub.starts_at,
            ends_at=sub.ends_at,
        )
        await send_whatsapp_message(teacher.phone, msg, teacher_id=teacher_id, context="manual_receipt")

    return RedirectResponse(
        url=f"/admin/teachers/{teacher_id}?success=تم+تفعيل+الاشتراك+يدوياً+بنجاح",
        status_code=303,
    )


# ─── Subscriptions ───────────────────────────────────────────────────────────

@router.get("/subscriptions", response_class=HTMLResponse)
def admin_subscriptions(
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    subs = list_subscriptions(db, limit=500)
    now = datetime.utcnow()
    active_count = sum(1 for s in subs if s.status == "active" and s.ends_at and s.ends_at.replace(tzinfo=None) > now)
    inactive_count = len(subs) - active_count
    total_revenue = sum(float(s.amount_sar) for s in subs if s.status == "active")

    enriched = []
    for s in subs:
        t = s.teacher
        enriched.append({
            "id": s.id,
            "teacher_id": s.teacher_id,
            "teacher_name": t.name if t else "—",
            "teacher_phone": t.phone if t else "—",
            "status": s.status,
            "plan_slug": s.plan_slug,
            "amount_sar": float(s.amount_sar),
            "starts_at": s.starts_at.strftime("%Y/%m/%d") if s.starts_at else None,
            "ends_at": s.ends_at.strftime("%Y/%m/%d") if s.ends_at else None,
            "payment_provider": s.payment_provider,
        })

    return templates.TemplateResponse("admin/subscriptions.html", {
        "request": request, "active": "subscriptions",
        "subscriptions": enriched, "total": len(enriched),
        "active_count": active_count, "inactive_count": inactive_count,
        "total_revenue": round(total_revenue, 2),
    })
