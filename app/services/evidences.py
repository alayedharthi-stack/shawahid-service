from sqlalchemy.orm import Session
from fastapi import HTTPException, status
from app.models.evidence import Evidence


ALLOWED_CATEGORIES = [
    "التخطيط",
    "التنفيذ داخل الصف",
    "التعلم التعاوني",
    "التعلم بالممارسة",
    "التقويم",
    "التحفيز",
    "التواصل مع أولياء الأمور",
    "سجل المتابعة",
    "الدورات والشهادات",
    "المبادرات والأنشطة",
    "أخرى",
]


def create_evidence(db: Session, teacher_id: int, source_phone: str, **kwargs) -> Evidence:
    """Create an evidence record. teacher_id is mandatory — enforced here."""
    if not teacher_id:
        raise ValueError("teacher_id is required before creating evidence")
    evidence = Evidence(teacher_id=teacher_id, source_phone=source_phone, **kwargs)
    db.add(evidence)
    db.commit()
    db.refresh(evidence)
    return evidence


def get_teacher_evidences(db: Session, teacher_id: int, skip: int = 0, limit: int = 100) -> list[Evidence]:
    return (
        db.query(Evidence)
        .filter(Evidence.teacher_id == teacher_id)
        .order_by(Evidence.category, Evidence.created_at)
        .offset(skip)
        .limit(limit)
        .all()
    )


def get_evidence_by_id(db: Session, evidence_id: int) -> Evidence | None:
    return db.query(Evidence).filter(Evidence.id == evidence_id).first()


def update_evidence(db: Session, evidence: Evidence, data: dict, current_teacher_id: int | None = None) -> Evidence:
    """Update evidence fields. Optionally verifies ownership."""
    if current_teacher_id is not None and evidence.teacher_id != current_teacher_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="ليس لديك صلاحية تعديل هذا الشاهد")
    for key, value in data.items():
        if value is not None and hasattr(evidence, key):
            setattr(evidence, key, value)
    db.commit()
    db.refresh(evidence)
    return evidence


def delete_evidence(db: Session, evidence: Evidence, current_teacher_id: int | None = None) -> None:
    if current_teacher_id is not None and evidence.teacher_id != current_teacher_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="ليس لديك صلاحية حذف هذا الشاهد")
    db.delete(evidence)
    db.commit()


def update_evidence_ai(
    db: Session,
    evidence_id: int,
    ai_result: dict,
    ai_status: str = "completed",
) -> None:
    """
    Persist AI classification results onto an evidence record.
    Only updates fields that have non-empty values in ai_result.
    ai_status should be 'completed' for OpenAI success or 'fallback' for rule-based.
    """
    evidence = db.query(Evidence).filter(Evidence.id == evidence_id).first()
    if not evidence:
        return
    # Only overwrite non-empty AI values; preserve any manual edits already on the record
    if ai_result.get("category"):
        evidence.category = ai_result["category"]
    if ai_result.get("title"):
        evidence.title = ai_result["title"]
    if ai_result.get("description"):
        evidence.description = ai_result["description"]
    if ai_result.get("grade"):
        evidence.grade = ai_result["grade"]
    if ai_result.get("subject"):
        evidence.subject = ai_result["subject"]
    if ai_result.get("evidence_type"):
        evidence.evidence_type = ai_result["evidence_type"]
    evidence.ai_raw = ai_result
    evidence.ai_status = ai_status
    db.commit()
