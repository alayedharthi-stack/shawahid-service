"""Segmented WhatsApp follow-up messages for inactive unpaid users."""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.evidence import Evidence
from app.models.portfolio_export import PortfolioExport
from app.models.teacher import Teacher
from app.services.subscriptions import get_subscription_status
from app.services.whatsapp import send_whatsapp_message

logger = logging.getLogger(__name__)

FOLLOWUP_MIN_AGE_HOURS = 6
FOLLOWUP_MAX_AGE_HOURS = 12


_NOT_EXPORTED_MESSAGE = """شفت الشواهد اللي أرسلتها 👍
باقي خطوة بسيطة يطلع لك ملفك كامل 📘

اكتب:
صدر

وأجهزه لك مباشرة 👌"""

_EXPORTED_UNPAID_MESSAGE = """شفت ملفك وكان مرتب 👍
ولو تضيف عليه كم شاهد بيصير أقوى 📘

ولو حاب نسخة كاملة بدون قيود
أنا أساعدك 👌"""

_GENERAL_TRIAL_MESSAGE = """مرحبًا 👋

لاحظت أنك جرّبت شواهد AI ولم تكمل الاشتراك،
هل واجهت أي صعوبة أو استفسار؟ 🤔

📞 تقدر تتواصل مباشرة مع الدعم على واتساب:
966555901901

أو اكتب لي هنا، وأنا أساعدك فورًا 👍"""


def _as_aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _latest_activity(*values: datetime | None) -> datetime | None:
    aware_values = [value for value in (_as_aware(v) for v in values) if value]
    return max(aware_values) if aware_values else None


def _within_followup_window(last_activity_at: datetime | None, now: datetime) -> bool:
    last_activity_at = _as_aware(last_activity_at)
    if not last_activity_at:
        return False

    age = now - last_activity_at
    return (
        age >= timedelta(hours=FOLLOWUP_MIN_AGE_HOURS)
        and age <= timedelta(hours=FOLLOWUP_MAX_AGE_HOURS)
    )


def _teacher_followup_snapshot(db: Session, teacher: Teacher) -> dict:
    evidence_count = (
        db.query(func.count(Evidence.id))
        .filter(Evidence.teacher_id == teacher.id)
        .scalar()
        or 0
    )
    latest_evidence_at = (
        db.query(func.max(Evidence.created_at))
        .filter(Evidence.teacher_id == teacher.id)
        .scalar()
    )

    done_export_count = (
        db.query(func.count(PortfolioExport.id))
        .filter(
            PortfolioExport.teacher_id == teacher.id,
            PortfolioExport.status == "done",
        )
        .scalar()
        or 0
    )
    latest_export_at = (
        db.query(func.max(PortfolioExport.created_at))
        .filter(PortfolioExport.teacher_id == teacher.id)
        .scalar()
    )

    last_activity_at = _latest_activity(
        latest_evidence_at,
        latest_export_at,
        teacher.updated_at,
        teacher.created_at,
    )
    tried_service = bool(evidence_count > 0 or done_export_count > 0 or teacher.welcomed)

    return {
        "evidence_count": evidence_count,
        "done_export_count": done_export_count,
        "latest_evidence_at": latest_evidence_at,
        "latest_export_at": latest_export_at,
        "last_activity_at": last_activity_at,
        "tried_service": tried_service,
    }


def _select_followup_message(snapshot: dict) -> tuple[str, str] | None:
    evidence_count = snapshot["evidence_count"]
    done_export_count = snapshot["done_export_count"]

    if evidence_count > 0 and done_export_count == 0:
        return "evidences_without_export", _NOT_EXPORTED_MESSAGE
    if done_export_count > 0:
        return "exported_unpaid", _EXPORTED_UNPAID_MESSAGE
    if snapshot["tried_service"]:
        return "trial_unclear", _GENERAL_TRIAL_MESSAGE
    return None


async def run_inactive_user_followups(
    db: Session,
    *,
    now: datetime | None = None,
    limit: int = 100,
    dry_run: bool = False,
) -> dict:
    """Send one segmented follow-up to eligible unpaid teachers.

    The function is intentionally invoked externally (admin/cron) rather than
    from the inbound message path, so we do not message users immediately after
    they interact with the bot.
    """
    now = _as_aware(now) or datetime.now(timezone.utc)
    candidates = (
        db.query(Teacher)
        .filter(Teacher.followup_sent_at.is_(None))
        .order_by(Teacher.updated_at.asc())
        .limit(limit)
        .all()
    )

    result = {"checked": len(candidates), "eligible": 0, "sent": 0, "skipped": 0, "dry_run": dry_run}

    for teacher in candidates:
        sub_status = get_subscription_status(db, teacher.id)["status"]
        if sub_status == "active_paid":
            result["skipped"] += 1
            continue

        snapshot = _teacher_followup_snapshot(db, teacher)
        if not snapshot["tried_service"] or not _within_followup_window(snapshot["last_activity_at"], now):
            result["skipped"] += 1
            continue

        selected = _select_followup_message(snapshot)
        if not selected:
            result["skipped"] += 1
            continue

        segment, message = selected
        result["eligible"] += 1
        if dry_run:
            logger.info("[FOLLOWUP DRY RUN] teacher_id=%s segment=%s", teacher.id, segment)
            continue

        sent = await send_whatsapp_message(
            teacher.phone,
            message,
            teacher_id=teacher.id,
            context=f"segmented_followup:{segment}",
        )
        if sent:
            teacher.followup_sent_at = now
            db.commit()
            result["sent"] += 1
            logger.info("[FOLLOWUP SENT] teacher_id=%s segment=%s", teacher.id, segment)
        else:
            result["skipped"] += 1
            logger.warning("[FOLLOWUP FAILED] teacher_id=%s segment=%s", teacher.id, segment)

    return result
