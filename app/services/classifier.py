"""
Evidence classifier using OpenAI Structured Outputs.

Flow:
  1. classify_evidence(db, evidence_id) — main entry point (background task).
  2. Builds prompt + optional image payload.
  3. Calls OpenAI via client.beta.chat.completions.parse() with EvidenceClassification schema.
  4. On any failure → fallback_classify() uses rule-based heuristics.
  5. Persists result to evidence record; never mixes evidence across teachers.

Safety invariants:
  - Evidence is fetched by (id) and teacher_id is logged/asserted before any AI call.
  - One evidence → one OpenAI call, never batched across teachers.
  - API key absence → silent fallback, webhook continues normally.
"""

from __future__ import annotations

import base64
import logging
import re
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app.core.config import settings

logger = logging.getLogger(__name__)

# ── Allowed value sets ───────────────────────────────────────────────────────

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

ALLOWED_EVIDENCE_TYPES = ["image", "pdf", "document", "text", "video", "other"]

# ── Structured output schema ─────────────────────────────────────────────────

class EvidenceClassification(BaseModel):
    category: str = Field(
        description="One of the allowed Arabic category names",
    )
    title: str = Field(description="Short Arabic title (≤12 words)")
    description: str = Field(
        description="Formal Arabic description suitable for a teacher's evidence portfolio (2-4 sentences)"
    )
    grade: str = Field(default="", description="School grade if visible, else empty string")
    subject: str = Field(default="", description="Subject name if visible, else empty string")
    evidence_type: str = Field(description="One of: image, pdf, document, text, video, other")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0, description="Model confidence 0–1")

# ── System prompt ────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
أنت مساعد متخصص في تنظيم ملف شواهد المعلم في المدارس السعودية.
مهمتك تحليل النص أو الصورة أو الملف المرفق وتصنيفه ضمن محور مناسب، وكتابة عنوان ووصف رسمي مناسبين لملف الشواهد.
لا تخترع تفاصيل غير ظاهرة.
إذا كان الصف أو المادة غير واضحين، اترك الحقل فارغًا.
اكتب بالعربية الفصحى.
التزم بالتصنيفات والأنواع المحددة فقط.
أرجع JSON مطابقًا للـ schema فقط.

التصنيفات المسموحة:
التخطيط، التنفيذ داخل الصف، التعلم التعاوني، التعلم بالممارسة، التقويم، التحفيز،
التواصل مع أولياء الأمور، سجل المتابعة، الدورات والشهادات، المبادرات والأنشطة، أخرى.

أنواع الشواهد المسموحة:
image, pdf, document, text, video, other.

أمثلة:
- صورة طلاب يستخدمون المنقلة ⟶ التعلم بالممارسة / image
- سجل حضور ومشاركة ⟶ سجل المتابعة / image
- PDF توزيع منهج / خطة / منهج ⟶ التخطيط / pdf
- اختبار / مهمة أدائية / ورقة عمل ⟶ التقويم
- بطاقات نجم / تكريم ⟶ التحفيز / image
- رسالة لأولياء أمور ⟶ التواصل مع أولياء الأمور
- شهادة دورة تدريبية ⟶ الدورات والشهادات\
"""

# ── Rule-based fallback keywords ─────────────────────────────────────────────

_PLANNING_KW    = re.compile(r"توزيع|خطة|منهج|أسبوعي|تحضير|فهرس|مقرر|unit plan|lesson plan", re.I)
_EVAL_KW        = re.compile(r"اختبار|قياس|مهمة أدائية|ورقة عمل|تقويم|تقييم|نتائج|درجات|rubric|quiz|test|exam", re.I)
_MOTIVATION_KW  = re.compile(r"تحفيز|تكريم|نجم|جائزة|بطاقة|مكافأة|لوحة شرف|star|reward|honor", re.I)
_COOPERATIVE_KW = re.compile(r"تعاوني|مجموعات|فريق|زملاء|collaborative|group|team", re.I)
_PRACTICAL_KW   = re.compile(r"تطبيق|ممارسة|تجربة|مختبر|workshop|hands.on|practical|أداة|منقلة|مسطرة", re.I)
_FOLLOWUP_KW    = re.compile(r"سجل|متابعة|حضور|غياب|واجب|مشاركة|أدوات|سلوك|attendance|follow", re.I)
_PARENTS_KW     = re.compile(r"ولي|أمور|اجتماع|رسالة|parent|guardian|meeting|contact", re.I)
_TRAINING_KW    = re.compile(r"دورة|شهادة|تدريب|ورشة|certificate|training|workshop|course", re.I)
_INITIATIVE_KW  = re.compile(r"مبادرة|نشاط|فعالية|رحلة|زيارة|مسابقة|initiative|activity|event", re.I)
_EXECUTION_KW   = re.compile(r"تنفيذ|درس|شرح|لوحة|عرض|presentation|classroom|board", re.I)


def _rule_category(text: str) -> str:
    """Heuristic category from combined text signals."""
    t = (text or "").strip()
    if _PLANNING_KW.search(t):    return "التخطيط"
    if _EVAL_KW.search(t):        return "التقويم"
    if _MOTIVATION_KW.search(t):  return "التحفيز"
    if _FOLLOWUP_KW.search(t):    return "سجل المتابعة"
    if _COOPERATIVE_KW.search(t): return "التعلم التعاوني"
    if _PRACTICAL_KW.search(t):   return "التعلم بالممارسة"
    if _PARENTS_KW.search(t):     return "التواصل مع أولياء الأمور"
    if _TRAINING_KW.search(t):    return "الدورات والشهادات"
    if _INITIATIVE_KW.search(t):  return "المبادرات والأنشطة"
    if _EXECUTION_KW.search(t):   return "التنفيذ داخل الصف"
    return "أخرى"


def fallback_classify(
    evidence_type: str,
    message_text: str | None,
    file_name: str | None,
) -> EvidenceClassification:
    """
    Rule-based fallback. Used when:
      - OPENAI_API_KEY is absent
      - OpenAI call raises any exception
      - Parsed response fails validation
    """
    combined = " ".join(filter(None, [message_text, file_name]))
    category = _rule_category(combined)
    ev_type = evidence_type if evidence_type in ALLOWED_EVIDENCE_TYPES else "other"

    return EvidenceClassification(
        category=category,
        title="",
        description="",
        grade="",
        subject="",
        evidence_type=ev_type,
        confidence=0.0,
    )


# ── Image helpers ─────────────────────────────────────────────────────────────

def _image_content_from_path(storage_path: str) -> dict | None:
    """Encode a local image as base64 data URL for GPT Vision."""
    try:
        p = Path(storage_path)
        if not p.exists() or p.stat().st_size == 0:
            return None
        ext = p.suffix.lower().lstrip(".")
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                "gif": "image/gif", "webp": "image/webp"}.get(ext, "image/jpeg")
        b64 = base64.b64encode(p.read_bytes()).decode()
        return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}", "detail": "high"}}
    except Exception as exc:
        logger.warning("Could not encode image %s: %s", storage_path, exc)
        return None


def _image_content_from_url(url: str) -> dict:
    return {"type": "image_url", "image_url": {"url": url, "detail": "high"}}


# ── User message builder ──────────────────────────────────────────────────────

def _build_user_content(
    evidence_type: str,
    message_text: str | None,
    media_url: str | None,
    storage_path: str | None,
    file_name: str | None,
    mime_type: str | None,
) -> list[dict]:
    """
    Assemble the user message content for OpenAI.
    Images → include vision payload (base64 > URL).
    PDFs/documents → send filename + text hints only (MVP).
    """
    parts: list[dict] = []

    # Text description
    context_parts: list[str] = []
    if message_text:
        context_parts.append(f"نص الرسالة: {message_text}")
    if file_name:
        context_parts.append(f"اسم الملف: {file_name}")
    if mime_type:
        context_parts.append(f"نوع الملف: {mime_type}")
    if evidence_type:
        context_parts.append(f"نوع الشاهد: {evidence_type}")

    if context_parts:
        parts.append({"type": "text", "text": "\n".join(context_parts)})

    # Image payload
    if evidence_type == "image":
        img_content = None
        if storage_path:
            img_content = _image_content_from_path(storage_path)
        if img_content is None and media_url:
            img_content = _image_content_from_url(media_url)
        if img_content:
            parts.append(img_content)
        else:
            parts.append({"type": "text", "text": "(الصورة غير متاحة للمعاينة، صنّف بناءً على النص)"})

    # PDF: add keyword hint for rule-based assist inside the prompt
    if evidence_type == "pdf" and file_name:
        hint = _rule_category(file_name)
        parts.append({"type": "text", "text": f"تلميح أولي بناءً على اسم الملف: {hint}"})

    if not parts:
        parts.append({"type": "text", "text": "لا توجد بيانات كافية."})

    return parts


# ── OpenAI call ───────────────────────────────────────────────────────────────

async def _classify_with_openai(
    evidence_type: str,
    message_text: str | None,
    media_url: str | None,
    storage_path: str | None,
    file_name: str | None,
    mime_type: str | None,
) -> EvidenceClassification:
    """
    Call OpenAI with Structured Outputs (beta.chat.completions.parse).
    Raises on any failure — caller must catch and use fallback.
    """
    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key=settings.OPENAI_API_KEY,
        timeout=settings.OPENAI_TIMEOUT_SECONDS,
    )

    user_content = _build_user_content(
        evidence_type, message_text, media_url, storage_path, file_name, mime_type
    )

    response = await client.beta.chat.completions.parse(
        model=settings.OPENAI_CLASSIFIER_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        response_format=EvidenceClassification,
        max_tokens=600,
        temperature=0.2,
    )

    parsed = response.choices[0].message.parsed
    if parsed is None:
        raise ValueError("OpenAI returned null parsed result")

    # Sanitize: ensure values are within allowed sets
    if parsed.category not in ALLOWED_CATEGORIES:
        parsed.category = _rule_category(
            " ".join(filter(None, [message_text, file_name]))
        )
    if parsed.evidence_type not in ALLOWED_EVIDENCE_TYPES:
        parsed.evidence_type = evidence_type if evidence_type in ALLOWED_EVIDENCE_TYPES else "other"

    return parsed


# ── Main entry point ──────────────────────────────────────────────────────────

async def classify_evidence(
    db,
    evidence_id: int,
    message_text: str | None = None,
    image_url: str | None = None,
    evidence_type: str = "text",
) -> None:
    """
    Background task: classify a single evidence record.
    Fetches the record by ID, asserts teacher_id is set, calls OpenAI,
    falls back gracefully on any failure.

    Safety: only this evidence is sent to OpenAI — never batched with others.
    """
    from app.services.evidences import get_evidence_by_id, update_evidence_ai
    from app.models.evidence import Evidence

    ev: Evidence | None = get_evidence_by_id(db, evidence_id)
    if ev is None:
        logger.error("classify_evidence: evidence %d not found", evidence_id)
        return

    # Invariant: must have a teacher
    if not ev.teacher_id:
        logger.error("classify_evidence: evidence %d has no teacher_id — aborting", evidence_id)
        return

    logger.info(
        "Classifying evidence %d (teacher_id=%d, type=%s)",
        evidence_id, ev.teacher_id, ev.evidence_type or evidence_type,
    )

    etype = ev.evidence_type or evidence_type
    ai_status = "completed"
    result: EvidenceClassification

    if not settings.OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY not set — using rule-based fallback for evidence %d", evidence_id)
        result = fallback_classify(etype, ev.message_text, ev.file_name)
        ai_status = "fallback"
    else:
        try:
            result = await _classify_with_openai(
                evidence_type=etype,
                message_text=ev.message_text,
                media_url=ev.media_url,
                storage_path=ev.storage_path,
                file_name=ev.file_name,
                mime_type=ev.mime_type,
            )
        except Exception as exc:
            logger.error("OpenAI classification failed for evidence %d: %s", evidence_id, exc)
            result = fallback_classify(etype, ev.message_text, ev.file_name)
            ai_status = "fallback"

    update_evidence_ai(db, evidence_id, result.model_dump(), ai_status=ai_status)
    logger.info(
        "Evidence %d classified: category=%r ai_status=%s confidence=%.2f",
        evidence_id, result.category, ai_status, result.confidence,
    )
