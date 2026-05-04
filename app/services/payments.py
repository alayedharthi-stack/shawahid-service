"""CRUD helpers for the payment_attempts table."""
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models.payment_attempt import PaymentAttempt


def create_payment_attempt(
    db: Session,
    teacher_id: int,
    provider_payment_id: str | None,
    payment_url: str | None,
    raw_response: dict | None = None,
    metadata: dict | None = None,
    provider: str = "moyasar",
    amount_sar: float = 29.00,
) -> PaymentAttempt:
    attempt = PaymentAttempt(
        teacher_id=teacher_id,
        provider=provider,
        provider_payment_id=provider_payment_id,
        status="initiated",
        amount_sar=amount_sar,
        payment_url=payment_url,
        raw_response=raw_response,
        metadata=metadata,
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    return attempt


def update_payment_attempt_status(
    db: Session,
    provider_payment_id: str,
    status: str,
    raw_response: dict | None = None,
) -> PaymentAttempt | None:
    attempt = (
        db.query(PaymentAttempt)
        .filter(PaymentAttempt.provider_payment_id == provider_payment_id)
        .first()
    )
    if not attempt:
        return None
    attempt.status = status
    if raw_response is not None:
        attempt.raw_response = raw_response
    attempt.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(attempt)
    return attempt


def get_latest_payment_attempt(db: Session, teacher_id: int) -> PaymentAttempt | None:
    return (
        db.query(PaymentAttempt)
        .filter(PaymentAttempt.teacher_id == teacher_id)
        .order_by(PaymentAttempt.id.desc())
        .first()
    )


def list_payment_attempts(
    db: Session, teacher_id: int, limit: int = 10
) -> list[PaymentAttempt]:
    return (
        db.query(PaymentAttempt)
        .filter(PaymentAttempt.teacher_id == teacher_id)
        .order_by(PaymentAttempt.id.desc())
        .limit(limit)
        .all()
    )
