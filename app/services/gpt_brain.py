"""
GPT Brain — conversational AI engine for every inbound WhatsApp message.

Flow:  WhatsApp inbound → ask_gpt(text, context) → GPTDecision → execute

Retry strategy per model:
  attempt 1 → fails → wait 1.5s → attempt 2 → fails → try next model
  If ALL models exhausted → return _failure_decision() ("لحظة بس 🌿")
  Caller (webhook) then sends interim msg + schedules background retry.

Logs: [GPT REQUEST] [GPT SUCCESS] [GPT ERROR] [RETRY]
No rule-based fallback. GPT leads, code executes.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from pathlib import Path
from typing import TypedDict

from app.core.config import settings

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS_PER_MODEL = 2   # 2 tries per model before moving to the next
_RETRY_DELAY_SECONDS    = 1.5  # wait between retries

_MODEL_NOT_FOUND_HINTS = (
    "model_not_found", "invalid_model", "does not exist",
    "no such model", "model not found",
)


# ── Model chain ───────────────────────────────────────────────────────────────

def _get_model_chain() -> list[str]:
    """Primary model from env, then hardcoded fallbacks."""
    primary = settings.OPENAI_MODEL or "gpt-4o"
    chain: list[str] = [primary]
    for fb in ("gpt-4o", "gpt-4o-mini"):
        if fb not in chain:
            chain.append(fb)
    return chain


def _is_model_unavailable(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(hint in msg for hint in _MODEL_NOT_FOUND_HINTS)


# ── Decision schema ───────────────────────────────────────────────────────────

class GPTDecision(TypedDict):
    intent: str        # evidence|smalltalk|help|payment|my_files|my_data|edit_data|update_profile|failure
    should_save: bool
    reply: str
    title: str | None
    category: str | None
    confidence: float
    profile_update: dict | None


# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM_BASE = """\
أنت عقل شواهد AI — المتحدث الوحيد مع المعلم.
أنت المسؤول عن كتابة كل رد يُرسل للمستخدم. لا يوجد ردود جاهزة غيرك.

قواعد أساسية:
• تحدث بالعربية الطبيعية البشرية، وناد المستخدم باسمه دائمًا إن كان معروفًا.
• ردودك ذكية، دافئة، ومختصرة. لا تعيد صياغة المعلومات بشكل جاف.
• التحيات والحديث العابر ← رد طبيعي، لا تحفظها. (intent=smalltalk)
• "اسمي ..." أو أي معلومة شخصية ← intent=update_profile، لا تحفظها كشاهد.
• الصور المدرسية والمواقف التعليمية والوثائق ← قرر أنت إن كانت شاهدًا. (intent=evidence)
• لا تُقرر الحفظ إلا إذا كان المحتوى يستحق التوثيق فعلًا.
• لا تذكر أي شيء عن الاشتراك أو الدفع — هذا قرار النظام وليس أنت.

إذا كان المستخدم يسأل عن شواهده (my_files): استخدم العدد من السياق في ردك.
إذا كان يسأل عن بياناته (my_data): استخدم البيانات من السياق في ردك.
إذا طلب "تعديل بياناتي" (edit_data): اطلب منه إرسال البيانات بهذا الشكل.

إذا أرسل صورة: صِفها، حدد هل هي شاهد، واقترح عنوانًا وتصنيفًا في ردك.

إذا وصل [تفريغ صوتي من Whisper AI 🎙]:
• تعامل مع النص المفرَّغ كمحتوى الرسالة الأصلية.
• قيّم هل يستحق التوثيق كشاهد.
• في ردك: أكّد أنك استمعت للملاحظة الصوتية، واذكر موضوعها.
• مثال رد: "استلمت ملاحظتك الصوتية 🎙 يبدو أنها تتحدث عن [موضوع]. تم حفظها كشاهد بعنوان: [عنوان]"

إذا وصل [تفريغ مقطع مرئي من Whisper AI 🎬]:
• نفس التعليمات أعلاه ولكن أشِر إلى أنه مقطع مرئي.

تصنيفات الشواهد:
التخطيط، التنفيذ داخل الصف، التعلم التعاوني، التعلم بالممارسة، التقويم، التحفيز،
التواصل مع أولياء الأمور، سجل المتابعة، الدورات والشهادات، المبادرات والأنشطة، أخرى.

الـ intents:
- evidence      → شاهد (should_save=true)
- smalltalk     → تحية أو حديث عابر
- help          → سؤال عن الخدمة
- payment       → "تصدير" أو طلب الملف
- my_files      → "ملفي" أو استفسار عن الشواهد
- my_data       → "بياناتي"
- edit_data     → "تعديل بياناتي"
- update_profile → اسم أو معلومات شخصية

أرجع JSON فقط:
{
  "intent": "...",
  "should_save": false,
  "reply": "رد عربي بشري طبيعي",
  "title": "عنوان الشاهد أو null",
  "category": "التصنيف أو null",
  "confidence": 0.95,
  "profile_update": {"name": "...", "subject": "...", "stage": "...", "school_name": "...", "grades": "..."}
}\
"""


def _build_system_prompt(teacher_context: str) -> str:
    return f"{_SYSTEM_BASE}\n\n{teacher_context}"


def build_teacher_context(
    name: str | None,
    subject: str | None,
    stage: str | None,
    school_name: str | None = None,
    evidence_count: int | None = None,
) -> str:
    """
    Build teacher context for GPT.
    Does NOT include subscription status — that is a backend-only decision.
    GPT should never assume or mention subscription state.
    """
    lines = ["=== سياق المستخدم ==="]
    lines.append(f"الاسم: {name or 'غير معروف بعد'}")
    if subject:
        lines.append(f"المادة: {subject}")
    if stage:
        lines.append(f"المرحلة الدراسية: {stage}")
    if school_name:
        lines.append(f"المدرسة: {school_name}")
    if evidence_count is not None:
        lines.append(f"عدد الشواهد المحفوظة حتى الآن: {evidence_count}")
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
    transcript: str | None = None,  # Whisper transcript for audio/video messages
    is_video: bool = False,          # True if media was a video (affects prompt label)
) -> GPTDecision:
    """
    Send any inbound WhatsApp message to GPT for a decision.

    Retry strategy:
      For each model in chain: try up to _MAX_ATTEMPTS_PER_MODEL times
        with _RETRY_DELAY_SECONDS between attempts.
      If model unavailable (404/invalid_model): skip to next model immediately.
      If all models fail: return _failure_decision() → caller sends interim msg
        and schedules a background retry.

    Never uses rule-based fallback for the reply.
    """
    if not settings.OPENAI_API_KEY:
        logger.error(
            "[GPT ERROR] OPENAI_API_KEY is not set — cannot call GPT. "
            "Set this environment variable on Railway."
        )
        return _failure_decision()

    content = _build_content(text, storage_path, image_url, mime_type, file_name,
                             transcript=transcript, is_video=is_video)
    system  = _build_system_prompt(teacher_context)
    models  = _get_model_chain()

    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key=settings.OPENAI_API_KEY,
        timeout=float(settings.OPENAI_TIMEOUT_SECONDS),
    )

    for model in models:
        for attempt in range(1, _MAX_ATTEMPTS_PER_MODEL + 1):
            logger.info("[GPT REQUEST] model=%s attempt=%d/%d", model, attempt, _MAX_ATTEMPTS_PER_MODEL)
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
                raw  = response.choices[0].message.content or "{}"
                data = json.loads(raw)
                decision = _coerce(data)
                logger.info(
                    "[GPT SUCCESS] model=%s attempt=%d intent=%s",
                    model, attempt, decision["intent"],
                )
                return decision

            except Exception as exc:
                if _is_model_unavailable(exc):
                    logger.warning(
                        "[GPT ERROR] model=%s not available — skipping to next model. %s",
                        model, exc,
                    )
                    break  # don't retry this model

                logger.error(
                    "[GPT ERROR] model=%s attempt=%d/%d: %s",
                    model, attempt, _MAX_ATTEMPTS_PER_MODEL, exc,
                )
                if attempt < _MAX_ATTEMPTS_PER_MODEL:
                    logger.info(
                        "[RETRY] model=%s waiting %.1fs before attempt %d",
                        model, _RETRY_DELAY_SECONDS, attempt + 1,
                    )
                    await asyncio.sleep(_RETRY_DELAY_SECONDS)
                # else: fall through to next model

    logger.error("[GPT ERROR] All models and retries exhausted — returning failure decision")
    return _failure_decision()


# ── Content builder ───────────────────────────────────────────────────────────

def _build_content(
    text: str | None,
    storage_path: str | None,
    image_url: str | None,
    mime_type: str | None,
    file_name: str | None,
    *,
    transcript: str | None = None,
    is_video: bool = False,
) -> list[dict]:
    parts: list[dict] = []
    text_parts: list[str] = []

    # ── Transcript (audio / video) — takes the top slot ──────────────────────
    if transcript:
        label = "[تفريغ مقطع مرئي من Whisper AI 🎬]" if is_video else "[تفريغ صوتي من Whisper AI 🎙]"
        text_parts.append(f"{label}\n{transcript}")

    # ── Plain text / caption ──────────────────────────────────────────────────
    if text:
        text_parts.append(text)

    # ── File metadata (only when no transcript — avoids noise) ───────────────
    if not transcript:
        if file_name:
            text_parts.append(f"(اسم الملف: {file_name})")
        if mime_type and not (mime_type or "").startswith("image/"):
            text_parts.append(f"(نوع الملف: {mime_type})")

    if text_parts:
        parts.append({"type": "text", "text": "\n".join(text_parts)})

    # ── Image / thumbnail — base64 from local storage, URL as fallback ───────
    # For videos: storage_path is the thumbnail (.thumb.jpg), not the video file.
    # mime_type must be image/* for _encode_local_image to work; we override it.
    if storage_path:
        img_mime = "image/jpeg" if is_video else mime_type
        img = _encode_local_image(storage_path, img_mime)
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
        ext  = p.suffix.lower().lstrip(".")
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
    """Normalise raw GPT JSON into a typed GPTDecision. Use GPT reply as-is."""
    intent      = str(data.get("intent", "smalltalk"))
    should_save = bool(data.get("should_save", False))

    # Guard: these intents must never trigger evidence saving
    if intent in ("update_profile", "smalltalk", "help", "failure"):
        should_save = False

    # Use GPT's reply directly — never replace it with a canned string
    reply = (data.get("reply") or "").strip()
    if not reply:
        reply = "..."   # only if GPT returned empty (very rare)

    return {
        "intent":         intent,
        "should_save":    should_save,
        "reply":          reply,
        "title":          data.get("title") or None,
        "category":       data.get("category") or None,
        "confidence":     float(data.get("confidence", 0.5)),
        "profile_update": data.get("profile_update") or None,
    }


def _failure_decision() -> GPTDecision:
    """
    Returned only when ALL models and retries are exhausted.
    Webhook sends this interim message then schedules a background retry.
    """
    return {
        "intent":         "failure",
        "should_save":    False,
        "reply":          "يبدو أن هناك ضغطًا بسيطًا الآن 🌿 جرّب بعد لحظات",
        "title":          None,
        "category":       None,
        "confidence":     0.0,
        "profile_update": None,
    }
