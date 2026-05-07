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
    intent: str
    # intents: evidence | batch_save | batch_summary | url_link |
    #          smalltalk | help | payment | my_files | my_data | edit_data |
    #          update_profile | failure
    should_save: bool
    reply: str
    title: str | None
    description: str | None       # educational description → DB + PDF
    category: str | None
    sub_category: str | None      # more specific grouping within category
    grade: str | None             # المرحلة الدراسية (if extractable)
    subject: str | None           # المادة (if extractable)
    confidence: float
    profile_update: dict | None
    # ── AI-first semantic flags ──────────────────────────────────────
    is_system_instruction: bool   # true → command TO the system, not evidence
    is_lesson_plan: bool          # true → lesson plan / curriculum document
    is_low_quality: bool          # true → vague/meaningless, low educational value
    needs_reply: bool             # true → teacher expects a conversational reply
    reply_style: str              # "short" | "medium" | "full"


# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM_BASE = """\
أنت "عقل شواهد AI" — مشرف تربوي ذكي ومدير ملف إنجاز متخصص للمعلمين.
أنت المتحدث الوحيد مع المعلم. كل رد يُرسل للمستخدم يكتبه أنت فقط.

══ المبدأ الأساسي ══
أنت لا ترد على الرسائل فقط — أنت تجمع وتفهم وتحفظ وتنظم وتصدر.
تصرّف كمشرف تربوي محترف، لا كروبوت محادثة.
كل صورة أو رابط أو ملف أو صوت يرسله المعلم يجب أن يتحول إلى شاهد منظم.

══ قاعدة ذهبية لا استثناء منها ══
❗ أي صورة أو فيديو أو تسجيل صوتي أو ملف أو رابط وصلك:
→ should_save = true دائمًا بلا استثناء
→ حدد التصنيف والعنوان حتى لو لم تفهم المحتوى جيدًا
→ استخدم "نشاط صفي" تصنيفًا افتراضيًا إذا لم تتمكن من التحديد
→ لا تُرجع should_save=false مطلقًا للوسائط

❗ أي نص يُثبت أن المعلم يتواصل أو يعمل أو يوجّه:
→ أي رسالة فيها توجيه للطلاب، تحفيز، ثناء، تعليمات صفية، تواصل مع أولياء الأمور → should_save=true
→ استخدم التصنيف المناسب: "تواصل مع أولياء الأمور" أو "مشاركة طلابية" أو "نشاط صفي" أو "تكريم وتميز"
→ لا تحفظ تحية مجردة أو حديثًا عن النظام (تلك smalltalk). الفارق: هل يدل النص على نشاط تعليمي حقيقي؟

══ تحديث بيانات البروفايل من أي رسالة ══
أي معلومة تخص بيانات المعلم تظهر في النص أو تفريغ الصوت أو الفيديو يجب وضعها داخل profile_update حتى لو كانت الرسالة نفسها شاهدًا.
الحقول المسموحة:
name, subject, stage, grades, school_name, principal_name, region, education_admin

أمثلة مهمة:
• "أدرس رياضيات للصفوف الرابع والخامس والسادس"
  profile_update = { "subject": "رياضيات", "grades": "الرابع، الخامس، السادس" }
• "أنا في مدرسة الملك فهد ومديري الأستاذ أحمد"
  profile_update = { "school_name": "مدرسة الملك فهد", "principal_name": "الأستاذ أحمد" }
• "أنا في الرياض / إدارة تعليم الرياض"
  profile_update = { "region": "الرياض", "education_admin": "إدارة تعليم الرياض" }

إذا كانت الرسالة صوتًا فيه بيانات بروفايل + محتوى تعليمي:
→ احفظها كشاهد إذا لها قيمة توثيقية
→ وفي نفس JSON ضع profile_update بالقيم المستخرجة

إذا كانت الرسالة فقط تحديث بيانات، استخدم intent=update_profile مع should_save=false.

إذا كان الملف غير مكتمل (المنطقة أو إدارة التعليم غائبة)، يمكنك السؤال بلطف عند المناسبة:
"بالمناسبة، من أي منطقة أنت؟ حتى تكون بيانات ملفك كاملة."

══ ذاكرة السياق القصيرة ══
إذا سأل المعلم سؤال متابعة مثل: "هل حدثتها؟" أو "هل حفظتها؟"
فهو يقصد آخر عملية تحديث أو حفظ في السياق.
إذا وجدت "آخر تحديث بروفايل تم حفظه" في سياق المستخدم، أجب تأكيدًا واضحًا:
"نعم، تم تحديث بيانات ملفك: المادة رياضيات، والصفوف الرابع والخامس والسادس."
ولا ترد برد عام مثل "كيف يمكنني مساعدتك؟".
أي سؤال قصير مرتبط بالسياق السابق (مثل: "طيب؟"، "هل تم؟"، "وش صار؟") يجب تفسيره بناءً على آخر إجراء معروف، لا كتحية عامة.

══ من صنعك؟ ══
إذا سأل: "من صنعك؟" أو "من طورك؟" أو "من مؤسسك؟"
أجب نصًا:
"تم تطوير شواهد AI بواسطة الأستاذ تركي بن عايد الحارثي."

══ قواعد ثابتة ══
• تحدّث بالعربية الطبيعية الدافئة، وناد المعلم باسمه دائمًا إن كان معروفًا.
• لا تذكر أي شيء عن الاشتراك أو الدفع — هذا قرار النظام وليس أنت.
• لا تذكر أسماء الطلاب من الصور إلا إذا كتبها المعلم نصًا.
• لا تدّعي فهم الصوت إلا إذا وُجد تفريغ فعلي [تفريغ صوتي من Whisper AI 🎙].

══ وضع استقبال الدفعة (batch_save) ══
إذا أرسل المعلم صورة أو ملفًا أو رابطًا منفردًا بدون نص توضيحي، أو بدا أنه يرسل دفعة:
• احفظ الشاهد بهدوء (should_save=true).
• أرسل ردًّا مختصرًا جدًّا مثل: "📥 تم" أو "✅ محفوظ".
• لا تقاطع المعلم بردود طويلة.
• استخدم intent=batch_save.

══ تقرير الدفعة (batch_summary) ══
عندما يتوقف المعلم عن الإرسال ويبدأ بالكتابة (يسأل، يعلّق، يطلب ملخصًا):
أرسل تقريرًا مطمئنًا بهذا الشكل:
"تم استلام دفعة شواهدك بنجاح ✅
استلمت: [عدد ونوع الشواهد]

الملخص:
[وصف تربوي قصير لما يظهر]
العنوان المقترح: [...]
التصنيف المقترح: [...]

تم حفظها، يمكنك إرسال المزيد أو كتابة: صدر ملفي 📄"
• should_save=false (الحفظ تمّ سابقًا).
• intent=batch_summary.

══ تحليل الصور ══
حلّل الصورة تربويًا وليس بصريًا فقط. استخرج:
- نوع النشاط (حل تمارين، شرح، تعاون، تكريم، ورقة عمل، اختبار...)
- المادة الدراسية إن ظهرت
- مشاركة الطلاب والعمل الجماعي
- استخدام السبورة أو الوسائل التعليمية
ثم احفظ الصورة داخل الشاهد مع وصف مناسب (intent=evidence أو batch_save).

══ تحليل الروابط (url_link) ══
إذا أرسل المعلم رابطًا (يوتيوب، موقع، مستند...) سواء منفردًا أو مع نص:
• احفظه كشاهد (should_save=true).
• استخرج العنوان المحتمل من النص القريب.
• صنّفه: مصدر تعليمي / رابط إثرائي / واجب منزلي / درس فيديو.
• ربطه بالنشاط القريب إن أمكن.
• intent=url_link.

══ تحليل الملفات الصوتية ══
إذا وصل [تفريغ صوتي من Whisper AI 🎙]:
• تعامل مع النص المفرَّغ كمحتوى الرسالة الأصلية.
• قيّم هل يستحق التوثيق (should_save=true إذا كان تعليميًا).
• رد: "استلمت ملاحظتك الصوتية 🎙 تتحدث عن [موضوع]. تم حفظها كشاهد بعنوان: [عنوان]"

إذا وصل [تفريغ مقطع مرئي من Whisper AI 🎬]:
• نفس التعليمات أعلاه مع الإشارة إلى أنه مقطع مرئي.

══ تحليل الملفات (PDF / Word / صور الاختبارات) ══
• صنّف حسب المحتوى: اختبار / ورقة عمل / خطة درس / نشاط / خطاب / سجل متابعة.
• استخرج العنوان والمادة والصف إن وجدت.
• احفظ الملف كرابط مرفق داخل الشاهد (should_save=true).
• صور الاختبارات المتعددة = شاهد واحد.

══ تجميع الشواهد ══
• الرسائل المتتالية لنفس النشاط → شاهد واحد وليس عدة شواهد.
• نص قريب من صور → يُعامَل كعنوان أو تعليق للشاهد.
• مثال: "نجوم الرياضيات اليوم" + صور طلاب = شاهد واحد بعنوان "تكريم المتفوقين".

══ التصنيفات المعتمدة ══
نشاط صفي، تعلم تعاوني، حل تمارين، مشاركة طلابية، تكريم وتميز،
شرح درس، واجب منزلي، اختبار، ورقة عمل، تقويم،
مصدر تعليمي، رابط إثرائي، تواصل مع أولياء الأمور، ملف إداري، إنجاز طلابي.

══ الـ intents ══
- evidence        → شاهد واحد مع رد كامل (should_save=true)
- batch_save      → شاهد ضمن دفعة، رد مختصر جدًا (should_save=true)
- batch_summary   → ملخص الدفعة، لا حفظ جديد (should_save=false)
- url_link        → رابط/يوتيوب (should_save=true)
- smalltalk       → تحية مجردة أو حديث عن النظام — لا قيمة توثيقية (should_save=false)
- capabilities    → "ماذا تستطيع؟" أو "وش تسوي؟" (should_save=false)
- help            → سؤال عن الخدمة (should_save=false)
- payment         → "تصدير" أو "صدر ملفي" أو طلب PDF (should_save=false)
- my_files        → "ملفي" أو استفسار عن عدد الشواهد (should_save=false)
- my_data         → "بياناتي" (should_save=false)
- edit_data       → "تعديل بياناتي" (should_save=false)
- update_profile  → اسم، مادة، مرحلة، صفوف، مدرسة، مدير، منطقة، إدارة التعليم (should_save=false)

مثال profile_update للمنطقة وإدارة التعليم:
  { "region": "الرياض", "education_admin": "إدارة تعليم الرياض" }
مثال profile_update للاسم والمادة والصفوف:
  { "name": "تركي", "subject": "رياضيات", "stage": "المتوسطة", "grades": "الرابع، الخامس، السادس" }

══ ردّ "ماذا تستطيع؟" (intent=capabilities) ══
إذا سأل المعلم عن إمكانيات الخدمة بأي صيغة:
اكتب ردًّا يشمل:
• الإمكانيات الحالية: توثيق الشواهد وإنشاء ملف إنجاز PDF احترافي.
• الإمكانيات القادمة (لا تدّعِ أنها متوفرة الآن): أوراق عمل، اختبارات جاهزة، أنشطة صفية حسب المنهج.
• ختم بجملة تشجيعية مثل: "هدفي أكون مساعدك الكامل داخل الفصل 📚"

══ بعد رسائل المتابعة ══
إذا سأل المستخدم عن طريقة الخدمة أو كيف يستخدم شواهد:
أجب باختصار فقط:
"أرسل شاهد → كرر الإرسال → اكتب \"صدر\"
وأنا أرتّب لك ملف شواهد احترافي جاهز للطباعة 📘"

إذا قال: "ما فهمت" أو "وضح" أو "كيف يعني؟":
أعطه مثالًا بسيطًا جدًا:
"أرسل صورة من نشاط أو فيديو أو صوت، وبعد ما تخلص اكتب \"صدر\"."

إذا لم يسأل عن الطريقة، لا تشرح من نفسك ولا تفتح بيعًا مباشرًا.

══ الحقول الدلالية الجديدة (AI-first flags) ══

is_system_instruction:
  true  → الرسالة هي أمر للنظام وليست شاهدًا تعليميًا.
  أمثلة: "عدّل القالب", "لا تحفظ هذا", "غيّر المادة", "حدّث بياناتي", "وش فهمت؟"
  أي رسالة تبدو موجهة للنظام كتعليمات → true
  false → أي محتوى تعليمي حقيقي أو استفسار طبيعي

is_lesson_plan:
  true  → الملف أو النص هو خطة درس أو توزيع منهج أو خطة فصل.
  أمثلة: PDF يحتوي أهدافًا وخطوات، نص "هذه خطة الفصل الثاني"
  false → شاهد تنفيذي عادي

is_low_quality:
  true  → المحتوى ضعيف جدًا: غامض، قصير جدًا بلا معنى، أو لا يدل على نشاط حقيقي.
  أمثلة: صورة فارغة، نص "هههه", "تم", "موجودة", رسالة لا يمكن فهمها
  false → المحتوى ذو قيمة تعليمية حتى لو بسيط

needs_reply:
  true  → المعلم يتوقع ردًا (سأل سؤالاً، أرسل شيئًا يحتاج تأكيدًا، طلب معلومات)
  false → أرسل مجرد ملف/صورة ضمن دفعة بدون توقع رد طويل (batch_save)

reply_style:
  "short"  → دفعة أو تأكيد سريع (batch_save, تحديث بسيط)
  "medium" → إجابة لسؤال أو شاهد عادي
  "full"   → شاهد مفرد مهم أو سؤال عن إمكانيات أو طلب تفصيلي

sub_category:
  تصنيف فرعي أدق داخل category. مثال: category="نشاط صفي" و sub_category="تعلم تعاوني"
  null إذا لم يكن هناك تصنيف فرعي واضح.

══ الفلسفة الذكية ══
أنت المحلل الأول والوحيد. الكود لا يفكر — أنت تفكر.
كل قرار يبدأ منك: هل يُحفظ؟ هل يُرفض؟ هل يحتاج سؤالًا؟ هل هذا أمر للنظام؟
الكود يُنفّذ فقط ما تقرره.

أرجع JSON فقط (بدون أي نص خارجه):
{
  "intent": "...",
  "should_save": true,
  "reply": "رد عربي بشري طبيعي — مختصر للدفعة، كامل للشاهد المنفرد",
  "title": "عنوان الشاهد — جملة قصيرة وصفية — null إذا لم يكن شاهدًا",
  "description": "وصف تربوي مهني جملتين — null إذا لم يكن شاهدًا",
  "category": "أحد التصنيفات المعتمدة أو null",
  "sub_category": "تصنيف فرعي أدق أو null",
  "grade": "المرحلة الدراسية إن وُجدت أو null",
  "subject": "المادة الدراسية إن وُجدت أو null",
  "confidence": 0.95,
  "profile_update": null,
  "is_system_instruction": false,
  "is_lesson_plan": false,
  "is_low_quality": false,
  "needs_reply": true,
  "reply_style": "medium"
}

ملاحظة: profile_update يمكن أن يحتوي على أي مزيج من:
name, subject, stage, grades, school_name, principal_name, region, education_admin

══ قاعدة حرجة للأسماء العربية ══
عند استخراج الاسم (name) من تفريغ صوتي أو نص:
• استخدم الاسم حرفيًا كما ورد في التفريغ — لا تصحّح الإملاء ولا تغيّر الحروف.
• الأسماء العربية لها أشكال متعددة صحيحة: "عايد" ≠ "عائد"، "الحارثي" ≠ "الحارفي".
• تفريغ Whisper قد يخطئ في الأسماء — المعلم سيؤكدها لاحقًا.
• لا تحاول "تصحيح" الاسم إملائيًا — فقط انقله كما هو.
• إذا كان الاسم غير واضح من التفريغ، ضع null في name واطلب الاسم نصًا.\
"""


def _build_system_prompt(teacher_context: str) -> str:
    return f"{_SYSTEM_BASE}\n\n{teacher_context}"


def build_teacher_context(
    name: str | None,
    subject: str | None,
    stage: str | None,
    grades: str | None = None,
    school_name: str | None = None,
    principal_name: str | None = None,
    region: str | None = None,
    education_admin: str | None = None,
    evidence_count: int | None = None,
    is_new_user: bool = False,
    last_profile_update: dict | None = None,
) -> str:
    """
    Build teacher context for GPT.
    Does NOT include subscription status — that is a backend-only decision.
    GPT should never assume or mention subscription state.
    """
    lines = ["=== سياق المستخدم ==="]
    lines.append(f"الاسم: {name or 'غير معروف بعد'}")
    if is_new_user:
        lines.append("مستخدم جديد: نعم (رسالته الأولى)")
    if subject:
        lines.append(f"المادة: {subject}")
    if stage:
        lines.append(f"المرحلة الدراسية: {stage}")
    if grades:
        lines.append(f"الصفوف: {grades}")
    if school_name:
        lines.append(f"المدرسة: {school_name}")
    if principal_name:
        lines.append(f"مدير المدرسة: {principal_name}")
    if region:
        lines.append(f"المنطقة: {region}")
    if education_admin:
        lines.append(f"إدارة التعليم: {education_admin}")
    if evidence_count is not None:
        lines.append(f"عدد الشواهد المحفوظة حتى الآن: {evidence_count}")
    if last_profile_update:
        lines.append(f"آخر تحديث بروفايل تم حفظه: {last_profile_update}")

    # Highlight missing profile fields so GPT can gently ask
    missing = []
    if not name:
        missing.append("الاسم")
    if not subject:
        missing.append("المادة")
    if not stage:
        missing.append("المرحلة")
    if not grades:
        missing.append("الصفوف")
    if not region:
        missing.append("المنطقة")
    if not education_admin:
        missing.append("إدارة التعليم")
    if missing:
        lines.append(f"حقول ملف المعلم غير مكتملة: {', '.join(missing)} — يمكنك سؤاله بشكل طبيعي إن سنحت الفرصة")

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
                    max_tokens=900,
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

_SAVE_INTENTS   = frozenset({"evidence", "batch_save", "url_link"})
_NOSAVE_INTENTS = frozenset({
    "update_profile", "smalltalk", "help", "capabilities", "failure",
    "batch_summary", "my_files", "my_data", "edit_data", "payment",
})


def _coerce(data: dict) -> GPTDecision:
    """
    Normalise raw GPT JSON into a typed GPTDecision.
    GPT's reply is used as-is — never replaced with canned strings.
    New semantic flags are surfaced to the caller so the rest of the system
    can rely on GPT's judgment rather than keyword rules.
    """
    intent      = str(data.get("intent", "smalltalk"))
    should_save = bool(data.get("should_save", False))

    # Enforce save rules based on intent
    if intent in _SAVE_INTENTS:
        should_save = True
    elif intent in _NOSAVE_INTENTS:
        should_save = False

    # System instructions are never evidence
    is_system_instruction = bool(data.get("is_system_instruction", False))
    if is_system_instruction:
        should_save = False

    reply = (data.get("reply") or "").strip()
    if not reply:
        reply = "📥" if intent == "batch_save" else "..."

    # reply_style defaults: batch_save → short, else medium
    raw_style = str(data.get("reply_style") or "")
    if raw_style in ("short", "medium", "full"):
        reply_style = raw_style
    else:
        reply_style = "short" if intent == "batch_save" else "medium"

    return {
        "intent":               intent,
        "should_save":          should_save,
        "reply":                reply,
        "title":                data.get("title") or None,
        "description":          data.get("description") or None,
        "category":             data.get("category") or None,
        "sub_category":         data.get("sub_category") or None,
        "grade":                data.get("grade") or None,
        "subject":              data.get("subject") or None,
        "confidence":           float(data.get("confidence", 0.5)),
        "profile_update":       data.get("profile_update") or None,
        "is_system_instruction": is_system_instruction,
        "is_lesson_plan":       bool(data.get("is_lesson_plan", False)),
        "is_low_quality":       bool(data.get("is_low_quality", False)),
        "needs_reply":          bool(data.get("needs_reply", True)),
        "reply_style":          reply_style,
    }


def _failure_decision() -> GPTDecision:
    """
    Returned only when ALL models and retries are exhausted.
    Webhook sends this interim message then schedules a background retry.
    """
    return {
        "intent":               "failure",
        "should_save":          False,
        "reply":                "يبدو أن هناك ضغطًا بسيطًا الآن 🌿 جرّب بعد لحظات",
        "title":                None,
        "description":          None,
        "category":             None,
        "sub_category":         None,
        "grade":                None,
        "subject":              None,
        "confidence":           0.0,
        "profile_update":       None,
        "is_system_instruction": False,
        "is_lesson_plan":       False,
        "is_low_quality":       False,
        "needs_reply":          True,
        "reply_style":          "medium",
    }


# ══════════════════════════════════════════════════════════════════════════════
# DEEP EVIDENCE ANALYSIS — Ministry of Education Standards Framework
# ══════════════════════════════════════════════════════════════════════════════

_MINISTRY_STANDARDS_KNOWLEDGE = """\
══ إطار الكفايات المهنية للمعلم — وزارة التعليم السعودية ══

المجال الأول: التخطيط
  المعيار 1.1 — إعداد خطط التعلم (الفصلية / الأسبوعية / الدرسية)
    مؤشرات الأداء: توافر وثائق التخطيط، وضوح الأهداف القابلة للقياس، ربط الأهداف بالمعايير الوطنية.
    شواهد نموذجية: خطط دراسية، توزيعات منهجية، تحضير الدروس، خرائط المفاهيم.

  المعيار 1.2 — التصميم التعليمي ومراعاة الفروق الفردية
    مؤشرات الأداء: تنويع مصادر التعلم، تكييف الأنشطة لمستويات مختلفة، استراتيجيات التعلم المتنوعة.
    شواهد نموذجية: أوراق عمل متدرجة، خطط علاجية، أنشطة إثرائية.

المجال الثاني: التنفيذ
  المعيار 2.1 — تهيئة بيئة التعلم
    مؤشرات الأداء: الجو التعليمي الإيجابي، الإدارة الصفية الفاعلة، الاستخدام الأمثل للمساحة والوقت.
    شواهد نموذجية: صور لترتيب الفصل، مقاطع لإدارة المجموعات، أنشطة تفاعلية.

  المعيار 2.2 — تنوع استراتيجيات التدريس
    مؤشرات الأداء: توظيف التعلم التعاوني، النشط، بالاكتشاف، حل المشكلات، التفكير الناقد.
    شواهد نموذجية: صور لمجموعات عمل، مشاريع، عروض طلابية، نقاشات صفية.

  المعيار 2.3 — توظيف التقنية ووسائل التعلم
    مؤشرات الأداء: استخدام العروض الرقمية، التقنيات التعليمية، مصادر الإنترنت، الوسائل التعليمية.
    شواهد نموذجية: روابط إثرائية، مقاطع فيديو تعليمية، تطبيقات رقمية، ألواح ذكية.

  المعيار 2.4 — التفاعل وتحفيز المشاركة
    مؤشرات الأداء: إشراك جميع الطلاب، تحفيز التفاعل، التعزيز الإيجابي، تكريم التميز.
    شواهد نموذجية: صور تكريم، شهادات طلابية، لوحات الشرف، أنشطة تنافسية.

المجال الثالث: التقويم
  المعيار 3.1 — تنوع أدوات التقويم
    مؤشرات الأداء: التقويم التكويني (أثناء التعلم)، الختامي (نهاية الوحدة)، مهام الأداء والمشاريع.
    شواهد نموذجية: اختبارات، أوراق عمل، مهام أدائية، ملاحظات مباشرة.

  المعيار 3.2 — تحليل نتائج المتعلمين وتشخيص مستوياتهم
    مؤشرات الأداء: قراءة بيانات الأداء، تحديد نقاط الضعف والقوة، بناء خطط علاجية.
    شواهد نموذجية: جداول رصد الدرجات، تحليل نتائج، خطط علاجية.

  المعيار 3.3 — التغذية الراجعة
    مؤشرات الأداء: تقديم تغذية راجعة فورية وبنّاءة، تمكين التقويم الذاتي لدى الطلاب.
    شواهد نموذجية: تصحيحات مع تعليقات، أوراق تقويم ذاتي.

المجال الرابع: البيئة والشراكة
  المعيار 4.1 — الإدارة الصفية والانضباط الإيجابي
    مؤشرات الأداء: قواعد واضحة، إدارة وقت الحصة، معالجة السلوك باحترافية.
    شواهد نموذجية: لوائح الصف، سجلات الحضور، وثائق إدارية.

  المعيار 4.2 — التواصل مع أولياء الأمور والمجتمع
    مؤشرات الأداء: التواصل الدوري، إشراك الأسرة، تبادل المعلومات.
    شواهد نموذجية: رسائل أولياء الأمور، اجتماعات، زيارات ميدانية.

  المعيار 4.3 — أداء الواجبات الوظيفية
    مؤشرات الأداء: الالتزام بالأنظمة واللوائح، تسليم الوثائق في وقتها، المشاركة المدرسية.
    شواهد نموذجية: ملفات إدارية، جداول، تقارير، شهادات مشاركة.

المجال الخامس: التطوير المهني
  المعيار 5.1 — التطوير الذاتي المستمر
    مؤشرات الأداء: الانخراط في الدورات، مجتمعات التعلم المهنية، البحث الإجرائي.
    شواهد نموذجية: شهادات دورات، تقارير بحثية، مبادرات تطويرية.

  المعيار 5.2 — التأمل المهني
    مؤشرات الأداء: تحليل الممارسات الذاتية، البحث عن التغذية الراجعة، التطوير المستمر للكفايات.
    شواهد نموذجية: يوميات مهنية، تقارير تأملية، ملاحظات تطويرية.\
"""

_DEEP_EVIDENCE_SYSTEM = """\
أنت خبير تربوي متخصص في تقييم أداء المعلمين وفق إطار الكفايات المهنية لوزارة التعليم السعودية.
مهمتك: تحليل كل شاهد من شواهد المعلم وتحويله إلى توثيق تربوي احترافي مرتبط بمعايير الوزارة الرسمية.

%MINISTRY_STANDARDS%

══ مبادئ التحليل الجوهرية ══
1. لا تُصدر حكمًا عامًا مثل "صورة توثيقية تبرز نشاطًا تعليميًا" — هذا ضعيف.
2. اقرأ الوصف والعنوان والسياق والنوع معًا قبل الحكم.
3. صورة طلاب حول طاولة قد تعني: تعلم تعاوني، أو تنفيذ درس، أو تقويم جماعي — اختر بناءً على السياق.
4. اربط كل شاهد بمعيار وزاري محدد، وليس تصنيفًا عامًا فقط.
5. الأثر على الطلاب يجب أن يكون واقعيًا، لا مبالغًا فيه.
6. استخدم لغة الدليل الرسمي، وليس عبارات إنشائية مكررة.
7. لا تصحّح أسماء الأشخاص (معلمين أو طلاب) — اتركها كما هي.

══ مرحلة التحليل ثم المراجعة الذاتية ══
اتبع هذا المنهج الإلزامي:
الخطوة أ) حلّل الشاهد وضع مسودة.
الخطوة ب) راجع نفسك:
  - هل الوصف خاص بهذا الشاهد أم قالب عام؟
  - هل المعيار الوزاري المختار دقيق أم تخميني؟
  - هل الأثر على الطلاب مقنع وواقعي؟
  - هل التأمل مهني حقيقي أم إنشاء فارغ؟
الخطوة ج) احذف أي جملة عامة وأعد كتابتها خاصة بهذا الشاهد فقط.\
"""

_DEEP_EVIDENCE_USER_TEMPLATE = """\
بيانات المعلم:
  الاسم: {teacher_name}
  المادة: {subject}
  المرحلة الدراسية: {stage}
  الصفوف: {grades}
  المدرسة: {school_name}

بيانات الشاهد:
  العنوان: {title}
  الوصف الأصلي: {description}
  التصنيف الحالي: {category}
  نوع الوسيط: {ev_type}
  رقم الشاهد في الملف: {ev_index} من {total_evs}

المحاور الموجودة في الملف كاملًا: {all_categories}

══ المطلوب ══
اكتب التحليل التربوي الاحترافي بالتنسيق التالي حرفيًا:

وصف الشاهد:
[وصف دقيق ومحدد لما يوثقه هذا الشاهد — جملة أو جملتان]

الهدف التربوي:
[الهدف الفعلي الذي يخدمه هذا الشاهد في إطار الكفايات المهنية]

الأثر على الطلاب:
[نتيجة أو ملاحظة واقعية تبيّن أثر هذا النشاط على التعلم]

تأمل المعلم:
[تحليل مهني صادق يُظهر وعي المعلم بممارسته وكيفية تطويرها]

الارتباط بمعايير الوزارة:
[المعيار الرسمي المباشر من إطار الكفايات — المجال ورقم المعيار والمؤشر]

ملاحظة التقييم:
[هل الشاهد قوي / متوسط / ضعيف؟ وسبب موجز في جملة واحدة]\
"""


async def analyze_evidence_deep(
    *,
    title: str | None = None,
    description: str | None = None,
    category: str | None = None,
    evidence_type: str | None = None,
    teacher_name: str | None = None,
    subject: str | None = None,
    stage: str | None = None,
    grades: str | None = None,
    school_name: str | None = None,
    all_categories: list[str] | None = None,
    evidence_index: int = 1,
    total_evidences: int = 1,
) -> str | None:
    """
    Deep single-evidence analysis using Ministry of Education standards.

    Uses the configured OPENAI_DEEP_MODEL (default: gpt-4o).
    Includes built-in self-review. Returns a formatted Arabic professional text.
    Falls back to None on any failure — caller uses the original description.

    This function is synchronous-blocking (uses OpenAI sync client) because
    it is called from a background task or a sync enrichment call.
    """
    if not settings.OPENAI_API_KEY:
        return None

    def _s(v: str | None, default: str = "غير محدد") -> str:
        v = (v or "").strip()
        return v if v and v.lower() not in ("null", "none", "undefined") else default

    system_prompt = _DEEP_EVIDENCE_SYSTEM.replace(
        "%MINISTRY_STANDARDS%", _MINISTRY_STANDARDS_KNOWLEDGE
    )

    categories_str = "، ".join(all_categories) if all_categories else "غير متوفر"

    user_prompt = _DEEP_EVIDENCE_USER_TEMPLATE.format(
        teacher_name=_s(teacher_name),
        subject=_s(subject),
        stage=_s(stage),
        grades=_s(grades),
        school_name=_s(school_name),
        title=_s(title),
        description=_s(description),
        category=_s(category),
        ev_type=_s(evidence_type, "نص"),
        ev_index=evidence_index,
        total_evs=total_evidences,
        all_categories=categories_str,
    )

    model = settings.OPENAI_DEEP_MODEL or settings.OPENAI_EXPORT_MODEL or "gpt-4o"

    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=settings.OPENAI_API_KEY,
            timeout=float(settings.OPENAI_TIMEOUT_SECONDS) * 2,  # allow extra time
        )
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            max_tokens=600,
            temperature=0.2,   # low temp = more consistent professional language
        )
        result = (response.choices[0].message.content or "").strip()
        logger.info(
            "[DEEP ANALYSIS] model=%s evidence_title=%r chars=%d",
            model, title, len(result),
        )
        return result or None
    except Exception as exc:
        logger.warning("[DEEP ANALYSIS FAILED] title=%r model=%s error=%s", title, model, exc)
        return None


# ── Portfolio-level intelligence ───────────────────────────────────────────────

_PORTFOLIO_ANALYSIS_SYSTEM = """\
أنت خبير تربوي متخصص في تقييم ملفات الإنجاز للمعلمين وفق معايير وزارة التعليم السعودية.
مهمتك: تحليل الملف كاملًا وكتابة تقرير تقييم مهني مختصر وعميق.

%MINISTRY_STANDARDS%

قواعد:
- لا تُكرر جملًا عامة. كل جملة يجب أن تكون مبنية على بيانات الملف الفعلية.
- استخدم أرقامًا وبيانات من الملف كلما أمكن.
- الأثر يجب أن يكون واقعيًا وليس مبالغًا فيه.
- اكتب بلغة الدليل الرسمي.\
"""

_PORTFOLIO_ANALYSIS_USER_TEMPLATE = """\
بيانات المعلم:
  الاسم: {teacher_name}
  المادة: {subject}
  المرحلة: {stage}
  الصفوف: {grades}
  المدرسة: {school_name}

إحصائيات الملف:
  إجمالي الشواهد: {total_count}
  توزيع المحاور: {categories_summary}
  توزيع الوسائط: {media_summary}
  المحور الأكثر: {top_category}
  المحاور الغائبة: {missing_categories}

نماذج من الشواهد (أبرز 5):
{sample_evidences}

══ المطلوب: تقرير تقييم الملف ══

اكتب التقرير بهذا التنسيق حرفيًا:

نقاط القوة:
- [نقطة قوة 1 مبنية على بيانات الملف الفعلية]
- [نقطة قوة 2]
- [نقطة قوة 3 إن وجدت]

مجالات التطوير:
- [مجال تطوير 1 محدد وقابل للتنفيذ]
- [مجال تطوير 2]

توصيات التطوير:
- [توصية 1 مرتبطة بمعيار وزاري محدد]
- [توصية 2]
- [توصية 3]

التقييم العام:
[فقرة موجزة (3-4 جمل) تصف مستوى الملف ونقاط البروز وأولويات التطوير]\
"""


def analyze_portfolio_sync(
    *,
    teacher_name: str | None = None,
    subject: str | None = None,
    stage: str | None = None,
    grades: str | None = None,
    school_name: str | None = None,
    total_count: int = 0,
    categories_summary: str = "",
    media_summary: str = "",
    top_category: str = "",
    missing_categories: str = "",
    sample_evidences: str = "",
) -> dict | None:
    """
    Portfolio-level AI analysis. Called once per export.
    Returns dict with keys: strengths, improvements, recommendations, overall_note.
    Falls back to None on failure.
    """
    if not settings.OPENAI_API_KEY or total_count == 0:
        return None

    def _s(v, default="غير محدد"):
        v = (v or "").strip()
        return v if v and v.lower() not in ("null", "none") else default

    system_prompt = _PORTFOLIO_ANALYSIS_SYSTEM.replace(
        "%MINISTRY_STANDARDS%", _MINISTRY_STANDARDS_KNOWLEDGE
    )

    user_prompt = _PORTFOLIO_ANALYSIS_USER_TEMPLATE.format(
        teacher_name=_s(teacher_name),
        subject=_s(subject),
        stage=_s(stage),
        grades=_s(grades),
        school_name=_s(school_name),
        total_count=total_count,
        categories_summary=categories_summary or "غير متوفر",
        media_summary=media_summary or "غير متوفر",
        top_category=top_category or "غير محدد",
        missing_categories=missing_categories or "لا يوجد",
        sample_evidences=sample_evidences or "لا توجد نماذج",
    )

    model = settings.OPENAI_DEEP_MODEL or settings.OPENAI_EXPORT_MODEL or "gpt-4o"

    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=settings.OPENAI_API_KEY,
            timeout=float(settings.OPENAI_TIMEOUT_SECONDS) * 3,
        )
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            max_tokens=800,
            temperature=0.2,
        )
        raw = (response.choices[0].message.content or "").strip()
        logger.info("[PORTFOLIO ANALYSIS] model=%s chars=%d", model, len(raw))

        # Parse the structured output into dict
        result: dict = {}
        current_key: str | None = None
        current_items: list[str] = []

        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("نقاط القوة:"):
                current_key = "strengths"
                current_items = []
            elif line.startswith("مجالات التطوير:"):
                if current_key == "strengths":
                    result["strengths"] = current_items[:]
                current_key = "improvements"
                current_items = []
            elif line.startswith("توصيات التطوير:"):
                if current_key == "improvements":
                    result["improvements"] = current_items[:]
                current_key = "recommendations"
                current_items = []
            elif line.startswith("التقييم العام:"):
                if current_key == "recommendations":
                    result["recommendations"] = current_items[:]
                current_key = "overall"
                current_items = []
            elif line.startswith("-") and current_key in ("strengths", "improvements", "recommendations"):
                item = line.lstrip("-").strip()
                if item:
                    current_items.append(item)
            elif current_key == "overall":
                current_items.append(line)

        if current_key == "overall" and current_items:
            result["overall_note"] = " ".join(current_items)
        elif current_key == "recommendations":
            result["recommendations"] = current_items[:]

        return result if result else None
    except Exception as exc:
        logger.warning("[PORTFOLIO ANALYSIS FAILED] error=%s", exc)
        return None
