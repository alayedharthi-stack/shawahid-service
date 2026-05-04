"""
GPT Brain — the sole decision-maker for every inbound WhatsApp message.

Flow:  WhatsApp inbound → ask_gpt() → GPTDecision → execute

GPT decides:
  - Is this evidence or smalltalk?
  - Should it be saved?
  - What title / category?
  - What reply to send?

No rules. No fallback heuristics. GPT leads, code executes.
"""
from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import TypedDict

from app.core.config import settings

logger = logging.getLogger(__name__)

# ── Decision schema ───────────────────────────────────────────────────────────

class GPTDecision(TypedDict):
    intent: str        # evidence | smalltalk | help | payment | my_files | my_data | edit_data
    should_save: bool
    reply: str
    title: str | None
    category: str | None
    confidence: float


# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
أنت عقل شواهد AI.
أنت المسؤول عن الفهم الكامل للمحادثة.
لا تنتظر أن يقول المستخدم "هذا شاهد".
افهم السياق بنفسك.

إذا كانت الرسالة أو الصورة مرتبطة ببيئة تعليمية أو موقف يستحق التوثيق، فاعتبرها شاهدًا.
إذا كانت مجرد تحية أو حديث عابر، فلا تحفظ.

تحدث بلغة عربية طبيعية، ذكية، ومهنية.
ولا تُرجع إلا JSON.

الـ intents المتاحة:
- evidence   → شاهد يستحق الحفظ في الملف
- smalltalk  → تحية أو كلام عابر
- help       → سؤال عن الخدمة
- payment    → طلب "تصدير" أو استفسار عن الاشتراك
- my_files   → "ملفي" أو استفسار عن الشواهد المحفوظة
- my_data    → "بياناتي" أو استفسار عن البيانات
- edit_data  → "تعديل بياناتي" أو طلب تعديل البيانات

تصنيفات الشواهد المتاحة:
التخطيط، التنفيذ داخل الصف، التعلم التعاوني، التعلم بالممارسة، التقويم، التحفيز،
التواصل مع أولياء الأمور، سجل المتابعة، الدورات والشهادات، المبادرات والأنشطة، أخرى.

أرجع JSON بهذا الشكل فقط:
{
  "intent": "...",
  "should_save": true,
  "reply": "رد عربي طبيعي",
  "title": "عنوان الشاهد أو null",
  "category": "أحد التصنيفات أو null",
  "confidence": 0.95
}\
"""


# ── Main entry point ──────────────────────────────────────────────────────────

async def ask_gpt(
    text: str | None,
    *,
    storage_path: str | None = None,   # local image path → base64 vision
    image_url: str | None = None,      # fallback if no storage_path
    mime_type: str | None = None,
    file_name: str | None = None,
) -> GPTDecision:
    """
    Send any inbound WhatsApp message to GPT for a decision.
    Images are passed as base64 (local storage) or URL for GPT Vision.
    Non-image files are described in text context only.
    Returns a GPTDecision — code just executes what GPT decides.
    """
    if not settings.OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY not set — defaulting to no-op decision")
        return _default_decision(text)

    content: list[dict] = []

    # Text / metadata part
    parts: list[str] = []
    if text:
        parts.append(text)
    if file_name:
        parts.append(f"(اسم الملف: {file_name})")
    if mime_type and not (mime_type or "").startswith("image/"):
        parts.append(f"(نوع الملف: {mime_type})")

    if parts:
        content.append({"type": "text", "text": "\n".join(parts)})

    # Image part — base64 from local storage has priority, then URL
    if storage_path:
        img = _encode_local_image(storage_path, mime_type)
        if img:
            content.append(img)
    if not any(p.get("type") == "image_url" for p in content) and image_url:
        content.append({
            "type": "image_url",
            "image_url": {"url": image_url, "detail": "high"},
        })

    if not content:
        content.append({"type": "text", "text": "(لا يوجد محتوى)"})

    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key=settings.OPENAI_API_KEY,
        timeout=float(settings.OPENAI_TIMEOUT_SECONDS),
    )

    try:
        response = await client.chat.completions.create(
            model=settings.OPENAI_CLASSIFIER_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": content},
            ],
            response_format={"type": "json_object"},
            max_tokens=500,
            temperature=0.2,
        )
        raw = response.choices[0].message.content or "{}"
        data = json.loads(raw)
        decision = _coerce(data)
        return decision

    except Exception as exc:
        logger.error("GPT brain call failed: %s", exc)
        return _default_decision(text)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _encode_local_image(storage_path: str, mime_type: str | None) -> dict | None:
    """Base64-encode a local image file for GPT Vision."""
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


def _coerce(data: dict) -> GPTDecision:
    """Normalise raw GPT JSON into a typed GPTDecision."""
    return {
        "intent":      str(data.get("intent", "smalltalk")),
        "should_save": bool(data.get("should_save", False)),
        "reply":       str(data.get("reply", "تم استلام رسالتك.")),
        "title":       data.get("title") or None,
        "category":    data.get("category") or None,
        "confidence":  float(data.get("confidence", 0.5)),
    }


def _default_decision(text: str | None) -> GPTDecision:
    """
    Used only when OPENAI_API_KEY is absent.
    Treats non-empty messages as evidence so nothing is silently lost.
    """
    has_content = bool((text or "").strip())
    return {
        "intent":      "evidence" if has_content else "smalltalk",
        "should_save": has_content,
        "reply": (
            "✅ تم استلام رسالتك وسيتم معالجتها قريبًا."
            if has_content else
            "أهلاً! أرسل لي أي شاهد أو صورة من عملك لأحفظه لك."
        ),
        "title":    None,
        "category": "أخرى" if has_content else None,
        "confidence": 0.0,
    }
