"""
exam_engine.messages — WhatsApp message builders for exam flow.

Phase-10 status: builders only. The webhook is not wired up to call
these yet (per the phase brief). They live here so the future
integration phase has a single, tested place to import from.

Pure module. No DB / GPT / network.
"""
from __future__ import annotations

from app.exam_engine.schemas import (
    EXAM_TYPE_LABELS_AR,
    GeneratedExam,
)


_FIELD_PROMPTS: dict[str, str] = {
    "subject": "📘 المادة",
    "grade": "🏫 الصف",
    "stage": "🏫 المرحلة",
    "exam_type": "📝 نوع الاختبار",
    "topic": "📚 الدرس أو الوحدة",
    "duration": "⏱️ الزمن (بالدقائق)",
    "marks": "🎯 الدرجة الكلية",
}


def build_exam_request_message() -> str:
    """The first message we send when a teacher asks for an exam."""
    return (
        "لإنشاء اختبار مناسب، أحتاج:\n"
        "📘 المادة\n"
        "🏫 الصف\n"
        "📝 نوع الاختبار\n"
        "📚 الدرس أو الوحدة"
    )


def build_exam_missing_info_message(missing: tuple[str, ...]) -> str:
    """Prompt only for the fields the request actually lacks."""
    if not missing:
        return build_exam_request_message()

    lines = ["لإكمال طلب الاختبار، أحتاج:"]
    for key in missing:
        lines.append(_FIELD_PROMPTS.get(key, f"• {key}"))
    return "\n".join(lines)


def build_exam_ready_message(exam: GeneratedExam) -> str:
    """Warm confirmation message sent right after the exam is ready.

    The download button is sent separately by the webhook, so this
    message only needs the headline + a short summary. Tone: friendly,
    teacher-to-teacher, light emoji — celebrates the moment.
    """
    label = EXAM_TYPE_LABELS_AR.get(
        exam.profile.exam_type, exam.profile.exam_type,
    )
    lines = [
        "تم تجهيز اختبارك بنجاح 🎉",
        "",
        f"📘 المادة: {exam.profile.subject or '—'}",
        f"🏫 الصف: {exam.profile.grade or '—'}",
        f"📝 النوع: {label}",
        f"❓ الأسئلة: {exam.question_count}",
        f"🎯 الدرجة: {exam.profile.total_marks}",
        f"⏱️ الزمن: {exam.profile.duration_minutes} دقيقة",
    ]
    if exam.warnings:
        lines.append("")
        lines.append("ملاحظات بسيطة:")
        lines.extend(f"• {w}" for w in exam.warnings)
    lines.append("")
    lines.append("تحت 👇 تجد زر تحميل الاختبار جاهزًا 📄")
    return "\n".join(lines)


def build_exam_download_button_body(exam: GeneratedExam | None = None) -> str:
    """Short body text shown above the download CTA button."""
    if exam is None:
        return "اختبارك جاهز للتحميل 📄"
    label = EXAM_TYPE_LABELS_AR.get(
        exam.profile.exam_type, exam.profile.exam_type,
    )
    return (
        f"اختبار {exam.profile.subject or '—'} للصف "
        f"{exam.profile.grade or '—'} ({label}) جاهز للتحميل 📄"
    )


def build_exam_download_text_fallback(
    *, download_url: str,
    subject: str | None = None,
    grade: str | None = None,
    exam_type: str | None = None,
) -> str:
    """Plain-text version of the download message — used when the
    interactive button cannot be sent (out of session, API down, etc.)."""
    head = "اختبارك جاهز للتحميل 📄"
    meta_parts: list[str] = []
    if subject:
        meta_parts.append(f"📘 {subject}")
    if grade:
        meta_parts.append(f"🏫 {grade}")
    if exam_type:
        label = EXAM_TYPE_LABELS_AR.get(exam_type, exam_type)
        meta_parts.append(f"📝 {label}")
    meta = "\n".join(meta_parts)
    return f"{head}\n{meta}\n\n{download_url}".strip()


def build_no_last_exam_message() -> str:
    """Reply when the teacher asks for the link but no exam was made yet."""
    return (
        "لا أجد اختبارًا جاهزًا في محادثتنا حتى الآن 🌿\n"
        "اكتب مثلًا:\n"
        "أنشئ اختبار رياضيات للصف الرابع نهائي\n"
        "وسأجهّزه لك خلال لحظات 📘"
    )


def build_exam_failure_message(reason: str, missing: tuple[str, ...] = ()) -> str:
    """Friendly fallback when generation fails."""
    if missing:
        return build_exam_missing_info_message(missing)
    return f"تعذّر إنشاء الاختبار:\n{reason}"


# ──────────────────────────────────────────────────────────────────────
# Phase-11: external-source flow messages
# ──────────────────────────────────────────────────────────────────────


def build_exam_source_selection_message(
    *,
    found_count: int,
    semester: str | None = None,
    exam_types: tuple[str, ...] = (),
) -> str:
    """Show the teacher which external sample types we matched."""
    if found_count <= 0:
        return (
            "لم أعثر على نماذج جاهزة من المصادر الخارجية ✋\n"
            "سأنشئ لك اختبارًا جديدًا من المنهج وخطة الدرس."
        )
    head = f"وجدت {found_count} نماذج مناسبة"
    if semester:
        head += f" لـ{semester}"
    head += " 📘\n"

    lines = [head]
    for label in exam_types:
        lines.append(f"✅ {label}")
    if not exam_types:
        lines.append("✅ نماذج اختبارات وزارية وإثرائية")
    lines.append("")
    lines.append("جارٍ إعداد نسخة مناسبة لك...")
    return "\n".join(lines)


def build_exam_generation_progress(
    *,
    stage: str,
    detail: str | None = None,
) -> str:
    """Short progress message used between fetch / quality / generation."""
    head_map = {
        "fetching": "🔍 جاري البحث في النماذج المتاحة...",
        "normalizing": "🛠️ جاري توحيد التنسيقات...",
        "quality_check": "✅ جاري فحص جودة الأسئلة...",
        "anti_copy": "🎯 جاري تكييف الأسئلة لتناسب صفك...",
        "rendering": "📄 جاري تجهيز ورقة الاختبار...",
    }
    head = head_map.get(stage, stage)
    if detail:
        return f"{head}\n{detail}"
    return head


__all__ = [
    "build_exam_request_message",
    "build_exam_missing_info_message",
    "build_exam_ready_message",
    "build_exam_failure_message",
    "build_exam_source_selection_message",
    "build_exam_generation_progress",
    "build_exam_download_button_body",
    "build_exam_download_text_fallback",
    "build_no_last_exam_message",
]
