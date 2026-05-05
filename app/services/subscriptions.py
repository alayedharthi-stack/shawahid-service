from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from app.models.subscription import TeacherSubscription
from app.models.payment_attempt import PaymentAttempt
from app.core.config import settings

# ─── Unified subscription status ─────────────────────────────────────────────
#
# Status values:
#   "active_paid" — active subscription backed by a verified real payment
#   "pending"     — subscription record exists but no verified payment (manual/trial)
#   "expired"     — subscription existed but has expired
#   "unpaid"      — no subscription record at all
#
# Only "active_paid" allows PDF export.
# ─────────────────────────────────────────────────────────────────────────────

def get_subscription_status(db: Session, teacher_id: int) -> dict:
    """
    Single source of truth for subscription gating.
    Used by webhook, admin panel, and any future access-control check.

    Returns dict:
      {
        "status": "active_paid" | "pending" | "expired" | "unpaid",
        "sub":     TeacherSubscription | None,
        "payment": PaymentAttempt | None,
      }
    """
    now = datetime.now(timezone.utc)

    # 1. Is there an active subscription at all?
    sub = (
        db.query(TeacherSubscription)
        .filter(
            TeacherSubscription.teacher_id == teacher_id,
            TeacherSubscription.status == "active",
            TeacherSubscription.ends_at > now,
        )
        .first()
    )

    if not sub:
        expired = (
            db.query(TeacherSubscription)
            .filter(TeacherSubscription.teacher_id == teacher_id)
            .order_by(TeacherSubscription.id.desc())
            .first()
        )
        if expired:
            return {"status": "expired", "sub": expired, "payment": None}
        return {"status": "unpaid", "sub": None, "payment": None}

    # 2. Subscription exists — verify it has a real paid PaymentAttempt.
    #    Manual activations from admin panel have payment_reference = None.
    if sub.payment_reference:
        payment = (
            db.query(PaymentAttempt)
            .filter(
                PaymentAttempt.teacher_id == teacher_id,
                PaymentAttempt.provider_payment_id == sub.payment_reference,
                PaymentAttempt.status == "paid",
            )
            .first()
        )
        if payment and float(payment.amount_sar or 0) >= (LAUNCH_AMOUNT_SAR - 0.01):
            return {"status": "active_paid", "sub": sub, "payment": payment}

    # Active subscription but no verified payment → manual/trial
    return {"status": "pending", "sub": sub, "payment": None}


def get_active_subscription(db: Session, teacher_id: int) -> TeacherSubscription | None:
    result = get_subscription_status(db, teacher_id)
    return result["sub"] if result["status"] == "active_paid" else None


def is_subscription_active(db: Session, teacher_id: int) -> bool:
    """Backward-compat wrapper. True only for active_paid."""
    return get_subscription_status(db, teacher_id)["status"] == "active_paid"


LAUNCH_PLAN_SLUG = "launch_annual_29"
LAUNCH_AMOUNT_SAR = 29.00
SUBSCRIPTION_DAYS = 365


def activate_subscription(
    db: Session,
    teacher_id: int,
    payment_provider: str | None = None,
    payment_reference: str | None = None,
    amount_sar: float = LAUNCH_AMOUNT_SAR,
    plan_slug: str = LAUNCH_PLAN_SLUG,
) -> TeacherSubscription:
    """Activate or extend annual subscription for teacher (launch offer: 29 SAR)."""
    now = datetime.now(timezone.utc)
    existing = get_active_subscription(db, teacher_id)
    if existing:
        existing.ends_at = existing.ends_at + timedelta(days=SUBSCRIPTION_DAYS)
        existing.payment_provider = payment_provider or existing.payment_provider
        existing.payment_reference = payment_reference or existing.payment_reference
        existing.updated_at = now
        db.commit()
        db.refresh(existing)
        return existing

    sub = TeacherSubscription(
        teacher_id=teacher_id,
        status="active",
        plan_slug=plan_slug,
        amount_sar=amount_sar,
        starts_at=now,
        ends_at=now + timedelta(days=SUBSCRIPTION_DAYS),
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
