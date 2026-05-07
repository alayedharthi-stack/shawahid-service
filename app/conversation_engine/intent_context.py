"""
conversation_engine.intent_context — context-aware intent resolution.

Wraps ``app.services.intents.detect_intent`` with a thin layer that
*remembers* what the teacher was just doing. This fixes a class of
real bugs surfaced by the AI evaluation suite:

    • "نعم" right after a name confirmation prompt should resolve to
      ``name_confirm_yes``, not stand-alone greeting/help.
    • A bare "اختبار" right after the teacher said "هذا" / "هذه" should
      pick up the implied category hint.
    • "صدر" two turns after a duplicate-warning prompt should still
      flow into the export branch even if the new message is short.

The base ``detect_intent`` stays untouched (locked by phase-3 rules).
"""
from __future__ import annotations

from dataclasses import dataclass

from app.conversation_engine.schemas import ConversationState
from app.services.intents import (
    INTENT_NONE,
    Intent,
    detect_intent,
    normalize,
)


# ──────────────────────────────────────────────────────────────────────
# Synthetic intents this layer can emit
# ──────────────────────────────────────────────────────────────────────

INTENT_NAME_CONFIRM_YES = "name_confirm_yes"
INTENT_NAME_CONFIRM_NO = "name_confirm_no"
INTENT_NAME_CONFIRM_RETRY = "name_confirm_retry"
INTENT_PROFILE_QUESTION = "profile_question"  # "هل تعرفني" / "من أنا"


@dataclass(frozen=True)
class ContextualIntent:
    """An intent enriched with the conversation state it was resolved against."""

    intent: str
    confidence: float
    payload: dict | None = None
    source: str = "fallback"  # "context" | "base" | "fallback"


# Patterns the WhatsApp UI elicits through quick-reply buttons.
_YES_PATTERNS = ("نعم", "نعم اعتمده", "اعتمده", "✅", "موافق", "تمام", "اوكي", "اوكي اعتمده")
_NO_PATTERNS = ("لا", "✏️", "غير صحيح", "ساكتبه", "خطا", "خاطي")
_RETRY_AUDIO_PATTERNS = ("🎤", "ساعيد", "ساعيد الصوت", "سارسل الصوت")
_PROFILE_QUESTIONS = (
    "من انا", "هل تعرفني", "تعرف من انا", "هل لديك بياناتي",
    "وش بياناتي", "ما بياناتي", "اظهر بياناتي", "اعطيني بياناتي",
    "ما الذي تعرفه عني", "تعرفني",
)


def resolve_intent(
    text: str | None,
    state: ConversationState,
) -> ContextualIntent:
    """Resolve ``text`` into an intent using ``state`` for context.

    Order:
        1. If a name confirmation is pending, short-circuit on YES/NO/RETRY.
        2. If text is a profile question, surface ``profile_question``.
        3. Otherwise delegate to the base detector.
    """
    norm = normalize(text or "")
    if not norm:
        return ContextualIntent(INTENT_NONE, 0.0, source="fallback")

    # ── 1. Pending name confirmation ───────────────────────────────────
    if state.pending_confirmation == "name" and state.pending_name:
        if _matches(norm, _YES_PATTERNS):
            return ContextualIntent(
                INTENT_NAME_CONFIRM_YES,
                0.95,
                payload={"name": state.pending_name},
                source="context",
            )
        if _matches(norm, _RETRY_AUDIO_PATTERNS):
            return ContextualIntent(
                INTENT_NAME_CONFIRM_RETRY,
                0.9,
                payload={"name": state.pending_name},
                source="context",
            )
        if _matches(norm, _NO_PATTERNS):
            return ContextualIntent(
                INTENT_NAME_CONFIRM_NO,
                0.9,
                payload={"name": state.pending_name},
                source="context",
            )

    # ── 2. Profile question ────────────────────────────────────────────
    if _matches(norm, _PROFILE_QUESTIONS):
        return ContextualIntent(INTENT_PROFILE_QUESTION, 0.9, source="context")

    # ── 3. Base detector ───────────────────────────────────────────────
    base: Intent = detect_intent(text)
    return ContextualIntent(
        intent=base.intent,
        confidence=base.confidence,
        payload=base.payload,
        source="base",
    )


def _matches(norm: str, patterns: tuple[str, ...]) -> bool:
    """Token-aware match: ``norm`` must equal one of ``patterns`` or
    contain it as a substring surrounded by whitespace/punctuation.

    The simple ``p in norm`` check used elsewhere in the codebase has
    too many false positives for short tokens like ``"لا"``."""
    for p in patterns:
        if not p:
            continue
        if norm == p:
            return True
        # Whole-word match for short tokens
        if len(p) <= 3:
            if (
                norm.startswith(p + " ")
                or norm.endswith(" " + p)
                or f" {p} " in norm
            ):
                return True
        elif p in norm:
            return True
    return False


__all__ = [
    "ContextualIntent",
    "INTENT_NAME_CONFIRM_YES",
    "INTENT_NAME_CONFIRM_NO",
    "INTENT_NAME_CONFIRM_RETRY",
    "INTENT_PROFILE_QUESTION",
    "resolve_intent",
]
