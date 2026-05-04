from sqlalchemy.orm import Session
from app.models.teacher import Teacher
from app.core.phone import normalize_phone


def get_or_create_teacher(db: Session, phone: str) -> Teacher:
    """
    Find teacher by normalized phone or create a new record.
    This is the primary isolation boundary — never call without a normalized phone.
    """
    normalized = normalize_phone(phone)
    teacher = db.query(Teacher).filter(Teacher.phone == normalized).first()
    if not teacher:
        teacher = Teacher(phone=normalized)
        db.add(teacher)
        db.commit()
        db.refresh(teacher)
    return teacher


def update_teacher(db: Session, teacher: Teacher, data: dict) -> Teacher:
    for key, value in data.items():
        if value is not None and hasattr(teacher, key):
            setattr(teacher, key, value)
    db.commit()
    db.refresh(teacher)
    return teacher


def get_teacher_by_id(db: Session, teacher_id: int) -> Teacher | None:
    return db.query(Teacher).filter(Teacher.id == teacher_id).first()
