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
from app.core.config import settings

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger(__name__)


def _sub_status(teacher: Teacher) -> str:
    now = datetime.utcnow()
    active = next(
        (s for s in teacher.subscriptions if s.status == "active" and s.ends_at and s.ends_at.replace(tzinfo=None) > now),
        None,
    )
    if active:
        return "active"
    expired = next((s for s in teacher.subscriptions), None)
    return "expired" if expired else "inactive"


def _sub_ends_at(teacher: Teacher) -> str | None:
    now = datetime.utcnow()
    active = next(
        (s for s in teacher.subscriptions if s.status == "active" and s.ends_at and s.ends_at.replace(tzinfo=None) > now),
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
            "sub_status": _sub_status(t),
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
        teachers.append({
            "id": t.id, "name": t.name, "phone": t.phone, "school_name": t.school_name,
            "subject": t.subject,
            "sub_status": _sub_status(t),
            "sub_ends_at": _sub_ends_at(t),
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

    return templates.TemplateResponse("admin/teacher_detail.html", {
        "request": request, "active": "teachers",
        "teacher": teacher, "evidences": evidences,
        "subscription": sub,
        "payment_attempts": payment_attempts,
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
                teacher_id=teacher_id,
                teacher_name=teacher.name or "",
            )
            payment_url = result["payment_url"]
            create_payment_attempt(
                db=db,
                teacher_id=teacher_id,
                provider_payment_id=result["provider_payment_id"],
                payment_url=payment_url,
                raw_response=result["raw_response"],
            )
        except Exception as exc:
            logger.error("Moyasar invoice creation failed for teacher %d: %s", teacher_id, exc)
            payment_url = get_payment_link(teacher_id)
    else:
        payment_url = get_payment_link(teacher_id)

    msg = build_payment_link_message(payment_url, teacher.name or "")
    await send_whatsapp_message(teacher.phone, msg)

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
