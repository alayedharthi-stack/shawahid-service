import logging

from sqlalchemy.orm import Session
from fastapi import HTTPException, status
from app.core.config import settings
from app.models.evidence import Evidence

logger = logging.getLogger(__name__)


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

_ENRICHMENT_PROMPT = """أنت خبير تربوي تابع لوزارة التعليم السعودية.

مهمتك: تحويل مدخلات المعلم (صورة / نص / صوت) إلى "شاهد تربوي احترافي" مطابق لمتطلبات تقييم الأداء الوظيفي.

يجب أن يحتوي كل شاهد على:

1) وصف الشاهد (مختصر ودقيق)
2) الهدف التربوي (مرتبط بمهارة تعليمية)
3) الأثر على الطلاب (نتائج فعلية أو ملاحظة واقعية)
4) تأمل المعلم (تحليل مهني صادق)
5) الارتباط بالمعايير (مثل: التخطيط، التنفيذ، التقويم)

قواعد مهمة:
- لا تستخدم عبارات عامة مثل "يوثق هذا الشاهد"
- اجعل اللغة مهنية واقعية
- اربط الشاهد بتحسين تعلم الطلاب
- لا تبالغ في النتائج
- اجعل النص مناسب للتقييم الرسمي
- اكتب بالعربية الفصحى"""

_TYPE_CATEGORY_HINT = {
    "image": "التنفيذ داخل الصف",
    "video": "التنفيذ داخل الصف",
    "pdf": "التخطيط أو التقويم",
    "document": "التخطيط أو التقويم",
    "file": "التخطيط أو التقويم",
    "url": "مصادر إثرائية",
}


def _clean(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"null", "none", "undefined"} else text


def _fallback_enrichment(evidence) -> str | None:
    desc = _clean(getattr(evidence, "description", None))
    return desc or None


def generate_ai_enriched_description(evidence) -> str | None:
    """Generate a professional Ministry-style description for one evidence.

    Safe by design: if OpenAI is unavailable or fails, return the existing
    description without interrupting saving.
    """
    fallback = _fallback_enrichment(evidence)
    if not settings.OPENAI_API_KEY:
        return fallback

    ev_type = _clean(getattr(evidence, "evidence_type", None)) or "text"
    category = _clean(getattr(evidence, "category", None))
    title = _clean(getattr(evidence, "title", None))
    description = _clean(getattr(evidence, "description", None))
    media_type_hint = _TYPE_CATEGORY_HINT.get(ev_type, category or "الأداء المهني")
    user_content = (
        f"العنوان: {title or 'غير محدد'}\n"
        f"الوصف الحالي: {description or 'غير محدد'}\n"
        f"التصنيف: {category or media_type_hint}\n"
        f"نوع الوسيط: {ev_type}\n"
        f"محور مقترح حسب النوع: {media_type_hint}\n\n"
        "اكتب الناتج في خمسة أسطر فقط بهذه العناوين حرفيًا:\n"
        "وصف الشاهد:\n"
        "الهدف التربوي:\n"
        "الأثر على الطلاب:\n"
        "تأمل المعلم:\n"
        "الارتباط بالمعايير:"
    )

    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=settings.OPENAI_API_KEY,
            timeout=float(settings.OPENAI_TIMEOUT_SECONDS),
        )
        response = client.chat.completions.create(
            model=settings.OPENAI_EXPORT_MODEL or settings.OPENAI_MODEL or "gpt-4o",
            messages=[
                {"role": "system", "content": _ENRICHMENT_PROMPT},
                {"role": "user", "content": user_content},
            ],
            max_tokens=450,
            temperature=0.25,
        )
        enriched = (response.choices[0].message.content or "").strip()
        return enriched or fallback
    except Exception as exc:
        logger.warning("[EVIDENCE ENRICHMENT FAILED] evidence_id=%s error=%s", getattr(evidence, "id", None), exc)
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
