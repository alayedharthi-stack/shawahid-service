from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.schemas.teacher import TeacherUpdate, TeacherOut
from app.schemas.evidence import EvidenceOut
from app.services.teachers import get_or_create_teacher, update_teacher, get_teacher_by_id
from app.services.evidences import get_teacher_evidences
from app.services.subscriptions import is_subscription_active, get_payment_link
from app.core.phone import normalize_phone
from app.services import exporter as exporter_svc

router = APIRouter(prefix="/teachers", tags=["teachers"])


@router.patch("/me", response_model=TeacherOut)
def update_teacher_profile(data: TeacherUpdate, db: Session = Depends(get_db)):
    """Update teacher profile. Phone is used as identity in MVP (no auth token yet)."""
    teacher = get_or_create_teacher(db, data.phone)
    updated = update_teacher(db, teacher, data.model_dump(exclude={"phone"}, exclude_none=True))
    return updated


@router.get("/{teacher_id}/evidences", response_model=list[EvidenceOut])
def list_teacher_evidences(
    teacher_id: int,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    """Return evidences belonging to teacher_id ONLY."""
    teacher = get_teacher_by_id(db, teacher_id)
    if not teacher:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="المعلم غير موجود")
    return get_teacher_evidences(db, teacher_id, skip=skip, limit=limit)


@router.post("/{teacher_id}/export")
async def export_portfolio(teacher_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Trigger PDF export. Requires active subscription."""
    teacher = get_teacher_by_id(db, teacher_id)
    if not teacher:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="المعلم غير موجود")

    if not is_subscription_active(db, teacher_id):
        link = get_payment_link(teacher_id)
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={"message": "الاشتراك غير مفعل", "payment_link": link},
        )

    export_record = exporter_svc.create_export_record(db, teacher_id)
    background_tasks.add_task(
        exporter_svc.run_export_background,
        db=db,
        teacher=teacher,
        export_id=export_record.id,
    )

    return {
        "ok": True,
        "export_id": export_record.id,
        "status": "processing",
        "message": "جارٍ إنشاء ملف الشواهد",
    }
