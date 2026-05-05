"""CRUD helpers for the payment_attempts table."""
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models.payment_attempt import PaymentAttempt
import logging

logger = logging.getLogger(__name__)


def create_payment_attempt(
    db: Session,
    teacher_id: int,
    provider_payment_id: str | None,
    payment_url: str | None,
    raw_response: dict | None = None,
    metadata: dict | None = None,
    provider: str = "moyasar",
    amount_sar: float = 29.00,
    status: str = "initiated",
) -> PaymentAttempt:
    attempt = PaymentAttempt(
        teacher_id=teacher_id,
        provider=provider,
        provider_payment_id=provider_payment_id,
        status=status,
        amount_sar=amount_sar,
        payment_url=payment_url,
        raw_response=raw_response,
        payment_metadata=metadata,  # DB column is `metadata`; Python attr renamed
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
    """Update status for an existing PaymentAttempt. Returns None if not found."""
    attempt = (
        db.query(PaymentAttempt)
        .filter(PaymentAttempt.provider_payment_id == provider_payment_id)
        .first()
    )
    if not attempt:
        logger.warning(
            "[PAYMENT] update_payment_attempt_status: no record found for "
            "provider_payment_id=%s — caller should create one",
            provider_payment_id,
        )
        return None
    attempt.status = status
    if raw_response is not None:
        attempt.raw_response = raw_response
    attempt.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(attempt)
    return attempt


def upsert_paid_payment_attempt(
    db: Session,
    teacher_id: int,
    provider_payment_id: str,
    amount_sar: float,
    raw_response: dict | None = None,
    metadata: dict | None = None,
    provider: str = "moyasar",
) -> PaymentAttempt:
    """
    Find existing PaymentAttempt by provider_payment_id and mark as paid,
    OR create a new one directly with status='paid'.

    Used when Moyasar webhook arrives for a payment we didn't pre-create a record for
    (e.g. user paid a 2nd invoice that replaced the 1st one).
    """
    attempt = (
        db.query(PaymentAttempt)
        .filter(PaymentAttempt.provider_payment_id == provider_payment_id)
        .first()
    )
    now = datetime.now(timezone.utc)
    if attempt:
        attempt.status = "paid"
        attempt.amount_sar = amount_sar
        if raw_response is not None:
            attempt.raw_response = raw_response
        attempt.updated_at = now
        logger.info("[PAYMENT] upsert: updated existing PA id=%d to paid", attempt.id)
    else:
        attempt = PaymentAttempt(
            teacher_id=teacher_id,
            provider=provider,
            provider_payment_id=provider_payment_id,
            status="paid",
            amount_sar=amount_sar,
            payment_url=None,
            raw_response=raw_response,
            payment_metadata=metadata,
        )
        db.add(attempt)
        logger.info(
            "[PAYMENT] upsert: created new PA for provider_payment_id=%s teacher_id=%d",
            provider_payment_id, teacher_id,
        )
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
