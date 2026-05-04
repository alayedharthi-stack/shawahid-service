from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from app.models.subscription import TeacherSubscription
from app.core.config import settings


def get_active_subscription(db: Session, teacher_id: int) -> TeacherSubscription | None:
    now = datetime.now(timezone.utc)
    return (
        db.query(TeacherSubscription)
        .filter(
            TeacherSubscription.teacher_id == teacher_id,
            TeacherSubscription.status == "active",
            TeacherSubscription.ends_at > now,
        )
        .first()
    )


def is_subscription_active(db: Session, teacher_id: int) -> bool:
    return get_active_subscription(db, teacher_id) is not None


def activate_subscription(
    db: Session,
    teacher_id: int,
    payment_provider: str | None = None,
    payment_reference: str | None = None,
    amount_sar: float = 49.00,
) -> TeacherSubscription:
    """Activate or extend annual subscription for teacher."""
    now = datetime.now(timezone.utc)
    existing = get_active_subscription(db, teacher_id)
    if existing:
        existing.ends_at = existing.ends_at + timedelta(days=365)
        existing.payment_provider = payment_provider or existing.payment_provider
        existing.payment_reference = payment_reference or existing.payment_reference
        existing.updated_at = now
        db.commit()
        db.refresh(existing)
        return existing

    sub = TeacherSubscription(
        teacher_id=teacher_id,
        status="active",
        plan_slug="annual_49",
        amount_sar=amount_sar,
        starts_at=now,
        ends_at=now + timedelta(days=365),
        payment_provider=payment_provider,
        payment_reference=payment_reference,
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


def get_payment_link(teacher_id: int) -> str:
    return settings.PAYMENT_LINK_TEMPLATE.format(teacher_id=teacher_id)


def list_subscriptions(db: Session, skip: int = 0, limit: int = 100) -> list[TeacherSubscription]:
    return db.query(TeacherSubscription).order_by(TeacherSubscription.id.desc()).offset(skip).limit(limit).all()
