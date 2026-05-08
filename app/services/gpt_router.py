"""
GPT-first decision layer for inbound WhatsApp messages.

Phase-13 contract
=================
Every inbound text (or voice transcript) is sent to GPT for ONE decision:

    "What action should the system take for this message?"

GPT replies with a structured ``GPTDecision``. The webhook then executes
exactly that action — nothing more.

This module is intentionally thin. It contains:

    1. The decision schema      (``GPTDecision`` / ``RouterContext``)
    2. A short, neutral prompt  (``_SYSTEM_PROMPT``)
    3. A lightweight context formatter
    4. The async GPT call       (``decide_next_action``)
    5. JSON validation          (``_coerce``)
    6. A simple fallback        (``_fallback_decision``) — used ONLY when
                                  GPT is unavailable / returns invalid JSON
    7. Logging

There are NO regex chains, NO Arabic phrase tables, NO action heuristics
in this file. Phrase-level recognition belongs to GPT — not to code.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Action constants
# ──────────────────────────────────────────────────────────────────────

ACTION_CHAT_REPLY        = "chat_reply"
ACTION_SAVE_EVIDENCE     = "save_evidence"
ACTION_CREATE_EXAM       = "create_exam"
ACTION_UPDATE_PROFILE    = "update_profile"
ACTION_EXPORT_PORTFOLIO  = "export_portfolio"
ACTION_REVIEW_PORTFOLIO  = "review_portfolio"
ACTION_DELETE_OR_EDIT    = "delete_or_edit"
ACTION_ASK_CLARIFICATION = "ask_clarification"
ACTION_UNKNOWN           = "unknown"

VALID_ACTIONS: frozenset[str] = frozenset({
    ACTION_CHAT_REPLY,
    ACTION_SAVE_EVIDENCE,
    ACTION_CREATE_EXAM,
    ACTION_UPDATE_PROFILE,
    ACTION_EXPORT_PORTFOLIO,
    ACTION_REVIEW_PORTFOLIO,
    ACTION_DELETE_OR_EDIT,
    ACTION_ASK_CLARIFICATION,
    ACTION_UNKNOWN,
})

# Source markers — observability only.
SOURCE_GPT      = "gpt"
SOURCE_FALLBACK = "fallback"   # GPT failed / invalid JSON
SOURCE_DISABLED = "disabled"   # OPENAI_API_KEY not configured


# ──────────────────────────────────────────────────────────────────────
# Public DTOs
# ──────────────────────────────────────────────────────────────────────


@dataclass
class RouterContext:
    """Lightweight context passed to GPT alongside the user message.

    Heavy ``teacher_context`` strings used by the deep brain are NOT
    duplicated here — the router stays cheap and predictable.
    """

    teacher_id: int = 0
    teacher_name: str | None = None
    teacher_subject: str | None = None
    teacher_stage: str | None = None
    teacher_grades: str | None = None
    teacher_school: str | None = None
    teacher_region: str | None = None
    teacher_education_admin: str | None = None
    has_media: bool = False
    media_type: str | None = None       # image / video / audio / pdf / document
    has_transcript: bool = False
    last_intent: str | None = None
    last_messages: tuple[str, ...] = ()  # most-recent-first, max 3
    pending_name_confirmation: bool = False
    pending_category_hint: str | None = None
    in_exam_flow: bool = False
    in_review_flow: bool = False
    awaiting_export_choice: bool = False


@dataclass
class GPTDecision:
    """Structured router decision. Producers must treat as read-only."""

    action: str = ACTION_UNKNOWN
    confidence: float = 0.0
    reply_text: str = ""
    should_save_evidence: bool = False

    # Action payloads — populated only for the matching action.
    evidence_hint: dict[str, Any] | None = None
    exam_request: dict[str, Any] | None = None
    profile_update: dict[str, Any] | None = None
    export_request: dict[str, Any] | None = None
    review_request: dict[str, Any] | None = None

    needs_clarification: bool = False
    clarification_question: str | None = None

    source: str = SOURCE_GPT
    raw: dict[str, Any] | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("raw", None)
        return d

    @property
    def is_save(self) -> bool:
        return self.action == ACTION_SAVE_EVIDENCE and self.should_save_evidence

    @property
    def is_pure_reply(self) -> bool:
        """True when the webhook should send ``reply_text`` and stop."""
        return self.action in (
            ACTION_CHAT_REPLY,
            ACTION_ASK_CLARIFICATION,
            ACTION_DELETE_OR_EDIT,
        )


# ──────────────────────────────────────────────────────────────────────
# System prompt — short, neutral. GPT does the thinking.
# ──────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
أنت موجِّه القرارات في "شواهد AI".
اقرأ رسالة المعلم وافهمها بحرية، ثم اختر إجراءً واحدًا فقط من القائمة:

- chat_reply         سؤال أو حوار عادي
- save_evidence      نشاط/ملف يستحق التوثيق
- create_exam        طلب إنشاء اختبار
- update_profile     تعريف ببيانات المعلم
- export_portfolio   طلب تصدير الملف
- review_portfolio   طلب مراجعة الملف
- delete_or_edit     طلب حذف/تعديل شاهد
- ask_clarification  المعنى غامض
- unknown            لم تتمكن من التحديد

قاعدة وحيدة: لا تجعل should_save_evidence = true إلا في حالة save_evidence فقط.
اكتب reply_text قصيرًا ودافئًا بالعربية (سطر إلى ثلاثة).
أرجع JSON فقط بهذه البنية:

{
  "action": "...",
  "confidence": 0.0,
  "reply_text": "...",
  "should_save_evidence": false,
  "evidence_hint":   { "category": "...", "title": "...", "description": "..." } | null,
  "exam_request":    { "subject": "...", "grade": "...", "stage": "...",
                       "exam_type": "...", "semester": "...", "unit": "..." } | null,
  "profile_update":  { "name": "...", "subject": "...", "stage": "...",
                       "grades": "...", "school_name": "...",
                       "region": "...", "education_admin": "..." } | null,
  "export_request":  { "mode": "full|smart|short" } | null,
  "review_request":  { } | null,
  "needs_clarification": false,
  "clarification_question": "..." | null
}
"""


# ──────────────────────────────────────────────────────────────────────
# Context formatter
# ──────────────────────────────────────────────────────────────────────


def _format_context(ctx: RouterContext) -> str:
    """Render ``RouterContext`` into a small Arabic block GPT can read."""
    lines: list[str] = ["=== سياق المحادثة ==="]

    if ctx.teacher_name:
        lines.append(f"اسم المعلم المعتمد: {ctx.teacher_name}")
    if ctx.teacher_subject:
        lines.append(f"المادة: {ctx.teacher_subject}")
    if ctx.teacher_stage:
        lines.append(f"المرحلة: {ctx.teacher_stage}")
    if ctx.teacher_grades:
        lines.append(f"الصفوف: {ctx.teacher_grades}")
    if ctx.teacher_school:
        lines.append(f"المدرسة: {ctx.teacher_school}")
    if ctx.teacher_region:
        lines.append(f"المنطقة: {ctx.teacher_region}")
    if ctx.teacher_education_admin:
        lines.append(f"إدارة التعليم: {ctx.teacher_education_admin}")

    if ctx.has_media:
        lines.append(f"يوجد ملف مرفق نوعه: {ctx.media_type or 'غير محدد'}")
    if ctx.has_transcript:
        lines.append("الرسالة تفريغ صوتي تلقائي.")

    if ctx.in_exam_flow:
        lines.append("المعلم حاليًا داخل تدفق إنشاء اختبار.")
    if ctx.in_review_flow:
        lines.append("المعلم حاليًا داخل تدفق المراجعة.")
    if ctx.awaiting_export_choice:
        lines.append("المعلم بانتظار اختيار طريقة التصدير.")
    if ctx.pending_name_confirmation:
        lines.append("هناك تأكيد اسم معلق.")
    if ctx.pending_category_hint:
        lines.append(f"تصنيف مقترح للشاهد القادم: {ctx.pending_category_hint}")

    if ctx.last_intent:
        lines.append(f"آخر نية مكتشفة: {ctx.last_intent}")
    if ctx.last_messages:
        joined = " | ".join(m[:60] for m in ctx.last_messages[:3])
        lines.append(f"آخر الرسائل: {joined}")

    lines.append("===================")
    return "\n".join(lines)


def _build_user_payload(message: str, ctx: RouterContext) -> str:
    return f"{_format_context(ctx)}\n\n=== رسالة المعلم ===\n{message.strip()}"


# ──────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────


async def decide_next_action(
    message: str | None,
    context: RouterContext | None = None,
    *,
    openai_client: Any = None,
    model: str | None = None,
) -> GPTDecision:
    """Single source of truth for what to do with an inbound message.

    GPT is the primary decision-maker. The deterministic fallback is
    used ONLY when OpenAI is unreachable or returns invalid JSON.
    """
    msg = (message or "").strip()
    ctx = context or RouterContext()

    if not msg:
        return GPTDecision(
            action=ACTION_UNKNOWN,
            confidence=0.0,
            reply_text="",
            source=SOURCE_FALLBACK,
            raw={"reason": "empty_message"},
        )

    if openai_client is None and not (settings.OPENAI_API_KEY or "").strip():
        logger.info("[GPT_ROUTER] OPENAI_API_KEY missing — using deterministic fallback.")
        decision = _fallback_decision(msg, ctx)
        decision.source = SOURCE_DISABLED
        return decision

    try:
        client = openai_client or _build_default_client()
        chosen_model = model or settings.OPENAI_MODEL or "gpt-4o-mini"
        response = await client.chat.completions.create(
            model=chosen_model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": _build_user_payload(msg, ctx)},
            ],
            response_format={"type": "json_object"},
            max_tokens=500,
            temperature=0.2,
        )
        raw = response.choices[0].message.content or "{}"
        data = json.loads(raw)
        decision = _coerce(data)
        logger.info(
            "[GPT_ROUTER] teacher_id=%d action=%s conf=%.2f save=%s",
            ctx.teacher_id, decision.action, decision.confidence,
            decision.should_save_evidence,
        )
        return decision

    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[GPT_ROUTER FAILED] teacher_id=%d err=%s — using deterministic fallback.",
            ctx.teacher_id, exc,
        )
        return _fallback_decision(msg, ctx)


# ──────────────────────────────────────────────────────────────────────
# Internals
# ──────────────────────────────────────────────────────────────────────


def _build_default_client() -> Any:
    from openai import AsyncOpenAI

    return AsyncOpenAI(
        api_key=settings.OPENAI_API_KEY,
        timeout=float(settings.OPENAI_TIMEOUT_SECONDS or 30),
    )


def _coerce(data: dict[str, Any]) -> GPTDecision:
    """Validate and normalise the raw GPT JSON.

    The only guard here is the ``should_save_evidence`` ↔ ``action``
    invariant. Everything else is trusted as GPT decided it.
    """
    raw_action = str(data.get("action") or "").strip().lower()
    if raw_action not in VALID_ACTIONS:
        raw_action = ACTION_UNKNOWN

    confidence = _safe_float(data.get("confidence"), 0.5)

    raw_save = bool(data.get("should_save_evidence", False))
    should_save = raw_save and raw_action == ACTION_SAVE_EVIDENCE

    reply_text = (data.get("reply_text") or "").strip()
    clarification_question = (data.get("clarification_question") or "").strip() or None
    needs_clarification = bool(data.get("needs_clarification", False)) \
        or raw_action == ACTION_ASK_CLARIFICATION

    return GPTDecision(
        action=raw_action,
        confidence=confidence,
        reply_text=reply_text,
        should_save_evidence=should_save,
        evidence_hint=_safe_dict(data.get("evidence_hint")),
        exam_request=_safe_dict(data.get("exam_request")),
        profile_update=_safe_dict(data.get("profile_update")),
        export_request=_safe_dict(data.get("export_request")),
        review_request=_safe_dict(data.get("review_request")),
        needs_clarification=needs_clarification,
        clarification_question=clarification_question,
        source=SOURCE_GPT,
        raw=data,
    )


def _safe_float(value: Any, default: float) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if out < 0.0:
        return 0.0
    if out > 1.0:
        return 1.0
    return out


def _safe_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict) and value:
        clean = {k: v for k, v in value.items() if v not in (None, "", [], {})}
        return clean or None
    return None


# ──────────────────────────────────────────────────────────────────────
# Deterministic fallback — used ONLY when GPT is unavailable.
# Keep this minimal: legacy intents resolver, no extra rules.
# ──────────────────────────────────────────────────────────────────────


def _fallback_decision(message: str, _ctx: RouterContext) -> GPTDecision:
    """Map :func:`intents.detect_intent` to a router action.

    This path runs only when GPT failed. It NEVER saves evidence — the
    webhook will simply send a polite reply and the next message can
    retry GPT.
    """
    from app.services import intents as intents_mod

    try:
        intent = intents_mod.detect_intent(message)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[GPT_ROUTER] fallback intent resolver failed: %s", exc)
        return GPTDecision(
            action=ACTION_UNKNOWN,
            confidence=0.0,
            reply_text="",
            source=SOURCE_FALLBACK,
            raw={"reason": "intent_resolver_failed", "error": str(exc)},
        )

    name = (intent.intent or intents_mod.INTENT_NONE) if intent else intents_mod.INTENT_NONE
    confidence = float(getattr(intent, "confidence", 0.0) or 0.0)

    action_map: dict[str, str] = {
        intents_mod.INTENT_CREATE_EXAM:       ACTION_CREATE_EXAM,
        intents_mod.INTENT_EXAM_MISSING_INFO: ACTION_CREATE_EXAM,
        intents_mod.INTENT_EXAM_CONFIRM:      ACTION_CREATE_EXAM,
        intents_mod.INTENT_EXAM_REGENERATE:   ACTION_CREATE_EXAM,
        intents_mod.INTENT_EXAM_EXPORT:       ACTION_EXPORT_PORTFOLIO,
        intents_mod.INTENT_EXPORT:            ACTION_EXPORT_PORTFOLIO,
        intents_mod.INTENT_REVIEW:            ACTION_REVIEW_PORTFOLIO,
        intents_mod.INTENT_DELETE_LAST:       ACTION_DELETE_OR_EDIT,
        intents_mod.INTENT_GREETING:          ACTION_CHAT_REPLY,
        intents_mod.INTENT_HELP:              ACTION_CHAT_REPLY,
        intents_mod.INTENT_NAME_CORRECTION:   ACTION_UPDATE_PROFILE,
    }
    action = action_map.get(name, ACTION_UNKNOWN)

    return GPTDecision(
        action=action,
        confidence=confidence,
        reply_text="",                  # webhook chooses a sane default
        should_save_evidence=False,     # fallback NEVER saves
        export_request=({"mode": None} if action == ACTION_EXPORT_PORTFOLIO else None),
        review_request=({} if action == ACTION_REVIEW_PORTFOLIO else None),
        source=SOURCE_FALLBACK,
        raw={"intent": name, "confidence": confidence},
    )


__all__ = [
    # Constants
    "ACTION_CHAT_REPLY",
    "ACTION_SAVE_EVIDENCE",
    "ACTION_CREATE_EXAM",
    "ACTION_UPDATE_PROFILE",
    "ACTION_EXPORT_PORTFOLIO",
    "ACTION_REVIEW_PORTFOLIO",
    "ACTION_DELETE_OR_EDIT",
    "ACTION_ASK_CLARIFICATION",
    "ACTION_UNKNOWN",
    "VALID_ACTIONS",
    "SOURCE_GPT",
    "SOURCE_FALLBACK",
    "SOURCE_DISABLED",
    # DTOs
    "GPTDecision",
    "RouterContext",
    # Public API
    "decide_next_action",
]
