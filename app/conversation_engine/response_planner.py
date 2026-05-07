"""
conversation_engine.response_planner — plan replies, never send them.

The planner inspects the resolved intent + state + profile and emits a
``ResponsePlan`` describing what the webhook should say next. The
webhook owns the actual ``send_whatsapp_message`` call.

Pure module. No DB, no GPT, no network.
"""
from __future__ import annotations

from app.conversation_engine.intent_context import (
    INTENT_NAME_CONFIRM_NO,
    INTENT_NAME_CONFIRM_RETRY,
    INTENT_NAME_CONFIRM_YES,
    INTENT_PROFILE_QUESTION,
    ContextualIntent,
)
from app.conversation_engine.name_intelligence import (
    NAME_CONFIDENCE_THRESHOLD,
    normalize_full_name,
)
from app.conversation_engine.schemas import (
    PLAN_KIND_ACK_PROFILE_UPDATE,
    PLAN_KIND_MISSING_PROFILE,
    PLAN_KIND_NAME_CONFIRM,
    PLAN_KIND_NONE,
    PLAN_KIND_PROFILE_ANSWER,
    ConversationState,
    ProfileContext,
    ProfileUpdate,
    ResponsePlan,
)


def plan_for_intent(
    intent: ContextualIntent,
    state: ConversationState,
    profile: ProfileContext,
) -> ResponsePlan:
    """Plan the response for a contextual intent."""
    if intent.intent == INTENT_PROFILE_QUESTION:
        return _plan_profile_answer(profile)

    if intent.intent == INTENT_NAME_CONFIRM_YES:
        # Ack handled by webhook (it persists the name); we just return
        # a ready reply text the webhook can choose to use.
        name = (intent.payload or {}).get("name") or state.pending_name or ""
        return ResponsePlan(
            kind=PLAN_KIND_ACK_PROFILE_UPDATE,
            reply_text=f"تم حفظ اسمك: {name} ✅\nسيظهر في ملف الشواهد بهذا الشكل.",
            payload={"field": "name", "value": name},
        )

    if intent.intent == INTENT_NAME_CONFIRM_NO:
        return ResponsePlan(
            kind=PLAN_KIND_NAME_CONFIRM,
            reply_text="تمام، أرسل اسمك مكتوبًا كما تحب أن يظهر بالضبط ✏️",
            payload={"action": "await_text_name"},
        )

    if intent.intent == INTENT_NAME_CONFIRM_RETRY:
        return ResponsePlan(
            kind=PLAN_KIND_NAME_CONFIRM,
            reply_text="تمام، أرسل التسجيل الصوتي مرة أخرى 🎤",
            payload={"action": "await_audio_name"},
        )

    return ResponsePlan(kind=PLAN_KIND_NONE)


def plan_for_profile_update(
    update: ProfileUpdate,
    state: ConversationState,
    profile: ProfileContext,
) -> ResponsePlan:
    """Plan the response when text contains a profile update.

    A new name with low confidence stages a confirmation; everything
    else gets an ack.
    """
    if update.is_empty():
        return ResponsePlan(kind=PLAN_KIND_NONE)

    if "name" in update.fields:
        candidate = normalize_full_name(update.fields["name"])
        if candidate.needs_confirmation or update.confidence < NAME_CONFIDENCE_THRESHOLD:
            return ResponsePlan(
                kind=PLAN_KIND_NAME_CONFIRM,
                reply_text=_build_name_confirmation(candidate.normalized or update.fields["name"]),
                payload={
                    "field": "name",
                    "value": candidate.normalized or update.fields["name"],
                    "confidence": candidate.confidence,
                },
            )

    return ResponsePlan(
        kind=PLAN_KIND_ACK_PROFILE_UPDATE,
        reply_text=_build_profile_ack(update),
        payload={"fields": dict(update.fields)},
    )


def plan_missing_profile(
    profile: ProfileContext,
) -> ResponsePlan:
    """Ask only for the fields we don't know yet."""
    missing = profile.missing_fields()
    if not missing:
        return ResponsePlan(kind=PLAN_KIND_NONE)

    questions: list[str] = []
    if "name" in missing:
        questions.append("• ما اسمك الكامل؟")
    if "subject" in missing:
        questions.append("• ما المادة التي تدرّسها؟")
    if "grades" in missing:
        questions.append("• ما المرحلة أو الصفوف التي تدرّسها؟")
    if "school" in missing:
        questions.append("• ما اسم مدرستك؟")

    body = "لإكمال ملفك، أحتاج بعض المعلومات:\n" + "\n".join(questions)
    return ResponsePlan(
        kind=PLAN_KIND_MISSING_PROFILE,
        reply_text=body,
        payload={"missing": list(missing)},
    )


# ──────────────────────────────────────────────────────────────────────
# Renderers
# ──────────────────────────────────────────────────────────────────────


def _plan_profile_answer(profile: ProfileContext) -> ResponsePlan:
    if not profile.teacher_name and not profile.subject and not profile.grades:
        return ResponsePlan(
            kind=PLAN_KIND_PROFILE_ANSWER,
            reply_text=(
                "لا أعرف بياناتك بعد ✍️\n"
                "أرسل اسمك الكامل والمادة التي تدرّسها لأبدأ بحفظ ملفك."
            ),
        )

    lines: list[str] = ["أعرف أنك:"]
    if profile.teacher_name:
        lines.append(f"👨‍🏫 الأستاذ {profile.teacher_name}")
    if profile.subject:
        lines.append(f"📘 معلم {profile.subject}")
    if profile.grades:
        lines.append(f"🏫 {' / '.join(profile.grades)}")
    elif profile.school_name:
        lines.append(f"🏫 {profile.school_name}")
    if profile.education_region:
        lines.append(f"📍 {profile.education_region}")

    return ResponsePlan(
        kind=PLAN_KIND_PROFILE_ANSWER,
        reply_text="\n".join(lines),
    )


def _build_name_confirmation(name: str) -> str:
    return (
        "هل تقصد:\n"
        f"\"{name}\" ؟\n\n"
        "✅ نعم، اعتمده\n"
        "✏️ لا، سأعيد كتابة الاسم\n"
        "🎤 سأرسل الصوت مرة أخرى"
    )


_FIELD_LABELS = {
    "name": "الاسم",
    "subject": "المادة",
    "grade": "المرحلة",
    "school": "المدرسة",
    "region": "المنطقة",
}


def _build_profile_ack(update: ProfileUpdate) -> str:
    lines = ["تم تحديث ملفك ✅"]
    for key, value in update.fields.items():
        label = _FIELD_LABELS.get(key, key)
        lines.append(f"• {label}: {value}")
    return "\n".join(lines)


__all__ = [
    "plan_for_intent",
    "plan_for_profile_update",
    "plan_missing_profile",
]
