"""
GPT Brain — conversational AI engine for every inbound WhatsApp message.

Flow:  WhatsApp inbound → ask_gpt(text, context) → GPTDecision → execute

GPT decides everything:
  - Is this evidence, smalltalk, a greeting, a profile update?
  - Should it be saved? What title/category?
  - What reply to send (using the teacher's name)?

Model chain: OPENAI_MODEL (env) → gpt-4.1 → gpt-4o
  - Only skips to next model when the current one is unavailable (404/invalid).
  - Any other failure → polite apology, nothing saved.

No rule-based fallback. GPT leads, code executes.
"""
from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import TypedDict

from app.core.config import settings

logger = logging.getLogger(__name__)

# ── Model fallback chain ──────────────────────────────────────────────────────

_MODEL_NOT_FOUND_HINTS = (
    "model_not_found", "invalid_model", "does not exist",
    "no such model", "404", "model not found",
)


def _get_model_chain() -> list[str]:
    """Primary model from env, then hardcoded fallbacks."""
    primary = settings.OPENAI_MODEL or settings.OPENAI_CLASSIFIER_MODEL or "gpt-4.1"
    chain: list[str] = [primary]
    for fb in ("gpt-4.1", "gpt-4o"):
        if fb not in chain:
            chain.append(fb)
    return chain


def _is_model_unavailable(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(hint in msg for hint in _MODEL_NOT_FOUND_HINTS)


# ── Decision schema ───────────────────────────────────────────────────────────

class GPTDecision(TypedDict):
    intent: str          # evidence|smalltalk|help|payment|my_files|my_data|edit_data|update_profile|failure
    should_save: bool
    reply: str
    title: str | None
    category: str | None
    confidence: float
    profile_update: dict | None   # {"name": "تركي", "subject": "..."}  — only for update_profile


# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM_BASE = """\
أنت عقل شواهد AI — مساعد شخصي ذكي للمعلم.
أنت المسؤول عن الفهم الكامل للمحادثة وإدارتها بذكاء واحترافية.

قواعد أساسية:
• تحدث بالعربية الطبيعية المهنية، وناد المستخدم باسمه إن كان معروفًا.
• التحيات والأسئلة الشخصية والحديث العابر ← ليست شواهد، لا تحفظها.
• "اسمي ..." أو "أنا فلان" أو أي معلومة شخصية ← نوع update_profile، لا تحفظها كشاهد.
• الصور والمواقف المدرسية والوثائق التعليمية ← GPT يقرر حفظها كشاهد.
• لا تُقرر الحفظ إلا إذا كنت متأكدًا أن المحتوى يستحق التوثيق في ملف الشواهد.

تصنيفات الشواهد المتاحة:
التخطيط، التنفيذ داخل الصف، التعلم التعاوني، التعلم بالممارسة، التقويم، التحفيز،
التواصل مع أولياء الأمور، سجل المتابعة، الدورات والشهادات، المبادرات والأنشطة، أخرى.

الـ intents المتاحة:
- evidence      → شاهد يستحق الحفظ (should_save=true)
- smalltalk     → تحية أو حديث عابر (should_save=false)
- help          → سؤال عن الخدمة
- payment       → "تصدير" أو طلب الاشتراك
- my_files      → "ملفي" أو استفسار عن الشواهد المحفوظة
- my_data       → "بياناتي" أو استفسار عن الحساب
- edit_data     → "تعديل بياناتي"
- update_profile → المستخدم يذكر اسمه أو معلومات شخصية عن نفسه

أرجع JSON فقط بهذا الشكل:
{
  "intent": "...",
  "should_save": false,
  "reply": "رد عربي طبيعي يخاطب المستخدم باسمه",
  "title": "عنوان الشاهد أو null",
  "category": "التصنيف أو null",
  "confidence": 0.95,
  "profile_update": {"name": "...", "subject": "...", "stage": "...", "school_name": "...", "grades": "..."} 
}\
"""


def _build_system_prompt(teacher_context: str) -> str:
    """Inject the live user context into the system prompt."""
    return f"{_SYSTEM_BASE}\n\n{teacher_context}"


def build_teacher_context(
    phone: str,
    name: str | None,
    subject: str | None,
    stage: str | None,
    sub_active: bool,
) -> str:
    """Build a short context block about the current teacher for GPT."""
    lines = ["=== سياق المستخدم ==="]
    lines.append(f"رقم الهاتف: {phone}")
    if name:
        lines.append(f"الاسم: {name}")
    else:
        lines.append("الاسم: غير معروف بعد")
    if subject:
        lines.append(f"المادة: {subject}")
    if stage:
        lines.append(f"المرحلة الدراسية: {stage}")
    lines.append(f"حالة الاشتراك: {'نشط ✅' if sub_active else 'غير مشترك'}")
    lines.append("===================")
    return "\n".join(lines)


# ── Main entry point ──────────────────────────────────────────────────────────

async def ask_gpt(
    text: str | None,
    *,
    teacher_context: str = "",
    storage_path: str | None = None,
    image_url: str | None = None,
    mime_type: str | None = None,
    file_name: str | None = None,
) -> GPTDecision:
    """
    Send any inbound WhatsApp message to GPT for a decision.
    Tries models in order: primary (OPENAI_MODEL) → gpt-4.1 → gpt-4o.
    On total failure: returns a polite apology — nothing is saved.
    """
    if not settings.OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY not set — returning failure decision")
        return _failure_decision()

    content = _build_content(text, storage_path, image_url, mime_type, file_name)
    system  = _build_system_prompt(teacher_context)

    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key=settings.OPENAI_API_KEY,
        timeout=float(settings.OPENAI_TIMEOUT_SECONDS),
    )

    for model in _get_model_chain():
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": content},
                ],
                response_format={"type": "json_object"},
                max_tokens=600,
                temperature=0.3,
            )
            logger.info("[GPT MODEL] model=%s", model)
            raw  = response.choices[0].message.content or "{}"
            data = json.loads(raw)
            return _coerce(data)

        except Exception as exc:
            if _is_model_unavailable(exc):
                logger.warning("Model %s unavailable, trying next in chain: %s", model, exc)
                continue
            # Non-model error (network, timeout, quota…) — stop and apologise
            logger.error("GPT brain call failed with model=%s: %s", model, exc)
            break

    return _failure_decision()


# ── Content builder ───────────────────────────────────────────────────────────

def _build_content(
    text: str | None,
    storage_path: str | None,
    image_url: str | None,
    mime_type: str | None,
    file_name: str | None,
) -> list[dict]:
    parts: list[dict] = []

    # Text / metadata
    text_parts: list[str] = []
    if text:
        text_parts.append(text)
    if file_name:
        text_parts.append(f"(اسم الملف: {file_name})")
    if mime_type and not (mime_type or "").startswith("image/"):
        text_parts.append(f"(نوع الملف: {mime_type})")
    if text_parts:
        parts.append({"type": "text", "text": "\n".join(text_parts)})

    # Image — base64 from local storage first, URL as fallback
    if storage_path:
        img = _encode_local_image(storage_path, mime_type)
        if img:
            parts.append(img)
    if not any(p.get("type") == "image_url" for p in parts) and image_url:
        parts.append({
            "type": "image_url",
            "image_url": {"url": image_url, "detail": "high"},
        })

    if not parts:
        parts.append({"type": "text", "text": "(لا يوجد محتوى)"})

    return parts


def _encode_local_image(storage_path: str, mime_type: str | None) -> dict | None:
    try:
        p = Path(storage_path)
        if not p.exists() or p.stat().st_size == 0:
            return None
        ext = p.suffix.lower().lstrip(".")
        mime = mime_type or {
            "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "png": "image/png",  "gif": "image/gif",
            "webp": "image/webp",
        }.get(ext, "image/jpeg")
        b64 = base64.b64encode(p.read_bytes()).decode()
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{b64}", "detail": "high"},
        }
    except Exception as exc:
        logger.warning("Could not encode image %s: %s", storage_path, exc)
        return None


# ── Response normalisation ────────────────────────────────────────────────────

def _coerce(data: dict) -> GPTDecision:
    """Normalise raw GPT JSON into a typed GPTDecision."""
    intent = str(data.get("intent", "smalltalk"))
    should_save = bool(data.get("should_save", False))

    # Guard: profile updates and smalltalk must never be saved as evidence
    if intent in ("update_profile", "smalltalk", "help"):
        should_save = False

    return {
        "intent":         intent,
        "should_save":    should_save,
        "reply":          str(data.get("reply", "تم استلام رسالتك.")),
        "title":          data.get("title") or None,
        "category":       data.get("category") or None,
        "confidence":     float(data.get("confidence", 0.5)),
        "profile_update": data.get("profile_update") or None,
    }


def _failure_decision() -> GPTDecision:
    """
    Returned when GPT is completely unavailable.
    No evidence saved, no rule-based guess — just a polite apology.
    """
    return {
        "intent":         "failure",
        "should_save":    False,
        "reply":          "عذرًا، واجهت مشكلة تقنية مؤقتة. أعد المحاولة بعد لحظات. 🙏",
        "title":          None,
        "category":       None,
        "confidence":     0.0,
        "profile_update": None,
    }
