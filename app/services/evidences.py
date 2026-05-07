import logging

from sqlalchemy.orm import Session
from fastapi import HTTPException, status
from app.core.config import settings
from app.models.evidence import Evidence

logger = logging.getLogger(__name__)

# ── Teacher context cache (set by webhook before calling create_evidence) ──────
# Lightweight in-memory store so enrichment has access to full teacher profile.
_ENRICHMENT_TEACHER_CONTEXT: dict[str, str | None] = {
    "name": None, "subject": None, "stage": None, "grades": None, "school_name": None,
}

def set_enrichment_teacher_context(
    *,
    name: str | None = None,
    subject: str | None = None,
    stage: str | None = None,
    grades: str | None = None,
    school_name: str | None = None,
) -> None:
    """Called by webhook before saving evidence so enrichment knows who the teacher is."""
    _ENRICHMENT_TEACHER_CONTEXT.update({
        "name": name, "subject": subject, "stage": stage,
        "grades": grades, "school_name": school_name,
    })


ALLOWED_CATEGORIES = [
    "نشاط صفي",
    "تعلم تعاوني",
    "حل تمارين",
    "مشاركة طلابية",
    "تكريم وتميز",
    "شرح درس",
    "واجب منزلي",
    "اختبار",
    "ورقة عمل",
    "تقويم",
    "مصدر تعليمي",
    "رابط إثرائي",
    "تواصل مع أولياء الأمور",
    "ملف إداري",
    "إنجاز طلابي",
    # Legacy categories (kept for backward compatibility with existing evidence records)
    "التخطيط",
    "التنفيذ داخل الصف",
    "التعلم التعاوني",
    "التعلم بالممارسة",
    "التحفيز",
    "سجل المتابعة",
    "الدورات والشهادات",
    "المبادرات والأنشطة",
    "أخرى",
]

def _clean(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"null", "none", "undefined"} else text


def _fallback_enrichment(evidence) -> str | None:
    desc = _clean(getattr(evidence, "description", None))
    return desc or None


def generate_ai_enriched_description(
    evidence,
    *,
    all_categories: list[str] | None = None,
    evidence_index: int = 1,
    total_evidences: int = 1,
) -> str | None:
    """
    Generate a deep Ministry-aligned professional description for one evidence.

    Uses analyze_evidence_deep() which:
    - Embeds full Saudi MOE Competency Framework knowledge
    - Links the evidence to specific official evaluation standards
    - Applies a built-in self-review phase to eliminate generic language
    - Uses OPENAI_DEEP_MODEL (configurable, default gpt-4o)

    Falls back to the original description on any failure.
    """
    fallback = _fallback_enrichment(evidence)
    if not settings.OPENAI_API_KEY:
        return fallback

    from app.services.gpt_brain import analyze_evidence_deep
    import asyncio

    ev_type    = _clean(getattr(evidence, "evidence_type", None)) or "text"
    category   = _clean(getattr(evidence, "category", None))
    title      = _clean(getattr(evidence, "title", None))
    description = _clean(getattr(evidence, "description", None))

    # Pull teacher context from the in-memory cache set by webhook
    ctx = _ENRICHMENT_TEACHER_CONTEXT

    try:
        # analyze_evidence_deep is an async function — run it in the event loop
        # if one is already running (FastAPI background task), otherwise create one.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # We're inside an async context (FastAPI background task) —
            # use run_in_executor to avoid blocking the event loop.
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                future = ex.submit(
                    asyncio.run,
                    analyze_evidence_deep(
                        title=title,
                        description=description,
                        category=category,
                        evidence_type=ev_type,
                        teacher_name=ctx.get("name"),
                        subject=ctx.get("subject"),
                        stage=ctx.get("stage"),
                        grades=ctx.get("grades"),
                        school_name=ctx.get("school_name"),
                        all_categories=all_categories,
                        evidence_index=evidence_index,
                        total_evidences=total_evidences,
                    ),
                )
                enriched = future.result(timeout=90)
        else:
            enriched = asyncio.run(
                analyze_evidence_deep(
                    title=title,
                    description=description,
                    category=category,
                    evidence_type=ev_type,
                    teacher_name=ctx.get("name"),
                    subject=ctx.get("subject"),
                    stage=ctx.get("stage"),
                    grades=ctx.get("grades"),
                    school_name=ctx.get("school_name"),
                    all_categories=all_categories,
                    evidence_index=evidence_index,
                    total_evidences=total_evidences,
                )
            )

        return enriched or fallback
    except Exception as exc:
        logger.warning(
            "[EVIDENCE ENRICHMENT FAILED] evidence_id=%s error=%s",
            getattr(evidence, "id", None), exc,
        )
        return fallback


def create_evidence(db: Session, teacher_id: int, source_phone: str, **kwargs) -> Evidence:
    """Create an evidence record. teacher_id is mandatory — enforced here."""
    if not teacher_id:
        raise ValueError("teacher_id is required before creating evidence")
    evidence = Evidence(teacher_id=teacher_id, source_phone=source_phone, **kwargs)
    if not evidence.ai_enriched_description:
        evidence.ai_enriched_description = generate_ai_enriched_description(evidence)
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


def verify_evidence_in_export(db: Session, evidence_id: int, teacher_id: int) -> bool:
    """
    Confirm an evidence record actually exists in the DB and would be picked
    up by the export query for this teacher.

    Returns True only if:
      • Row exists with matching id
      • teacher_id matches
      • is_excluded_from_export is False (or unset)

    This is the source of truth before we send any "saved successfully" message
    to the teacher. Never claim a save succeeded without calling this.
    """
    row = (
        db.query(Evidence)
        .filter(
            Evidence.id == evidence_id,
            Evidence.teacher_id == teacher_id,
        )
        .first()
    )
    if not row:
        return False
    if getattr(row, "is_excluded_from_export", False):
        return False
    return True


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
    if not evidence.ai_enriched_description:
        evidence.ai_enriched_description = generate_ai_enriched_description(evidence)
    evidence.ai_raw = ai_result
    evidence.ai_status = ai_status
    db.commit()
