"""
conversation_engine.schemas — Data Transfer Objects.

Phase-9 contract
================
Pure dataclasses. No DB, no network, no GPT, no Playwright.

These DTOs are the *only* surface other layers should see — never
import internal helpers from sibling modules directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# ──────────────────────────────────────────────────────────────────────
# Conversation memory
# ──────────────────────────────────────────────────────────────────────


@dataclass
class ConversationMessage:
    """One turn in the rolling conversation window.

    ``direction`` is ``"in"`` (teacher → bot) or ``"out"`` (bot → teacher).
    ``intent`` is one of ``app.services.intents.INTENT_*`` or ``None``
    when the message is media-only / unclassified.
    """

    text: str
    direction: str = "in"
    intent: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class ConversationState:
    """Rolling, in-memory state for a single teacher.

    The state is *advisory*. Losing it on restart is acceptable — every
    field has a safe ``None`` default. Persisting it would belong to a
    later phase (Redis / DB) once we measure that the heuristics
    actually need durability.
    """

    teacher_id: int
    last_intent: str | None = None
    last_category_hint: str | None = None
    last_uploaded_media: str | None = None
    last_profile_update: dict[str, str] | None = None
    pending_confirmation: str | None = None  # e.g. "name", "category"
    pending_name: str | None = None
    pending_action: str | None = None        # e.g. "export", "review"
    last_messages: list[ConversationMessage] = field(default_factory=list)
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc)


# ──────────────────────────────────────────────────────────────────────
# Profile intelligence
# ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProfileContext:
    """A snapshot of what we know about the teacher.

    Every field is optional. ``confidence`` is the per-field minimum —
    callers may choose to surface only fields whose own confidence is
    above some threshold.
    """

    teacher_name: str | None = None
    school_name: str | None = None
    subject: str | None = None
    grades: tuple[str, ...] = ()
    education_region: str | None = None
    confidence: float = 0.0

    def is_complete(self) -> bool:
        return bool(self.teacher_name and self.subject)

    def missing_fields(self) -> tuple[str, ...]:
        out: list[str] = []
        if not self.teacher_name:
            out.append("name")
        if not self.subject:
            out.append("subject")
        if not self.grades:
            out.append("grades")
        if not self.school_name:
            out.append("school")
        return tuple(out)


@dataclass(frozen=True)
class ProfileUpdate:
    """A single profile-update extraction from teacher text.

    ``fields`` only contains keys we actually detected; missing keys
    mean "no signal — don't touch the existing value".
    """

    fields: dict[str, str] = field(default_factory=dict)
    confidence: float = 0.0
    reason: str = ""

    def is_empty(self) -> bool:
        return not self.fields


# ──────────────────────────────────────────────────────────────────────
# Name intelligence
# ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class NameCandidate:
    """A single name normalisation suggestion.

    ``raw`` is the input as the teacher / Whisper produced it.
    ``normalized`` is the canonical Arabic spelling.
    ``confidence`` is 0-1; below the configured threshold the caller
    must ask the teacher to confirm before persisting.
    """

    raw: str
    normalized: str
    confidence: float
    reason: str = ""
    needs_confirmation: bool = False


# ──────────────────────────────────────────────────────────────────────
# Response planning
# ──────────────────────────────────────────────────────────────────────


# What kind of reply the planner wants the webhook to send.
PLAN_KIND_PROFILE_ANSWER = "profile_answer"
PLAN_KIND_NAME_CONFIRM = "name_confirm"
PLAN_KIND_MISSING_PROFILE = "missing_profile"
PLAN_KIND_ACK_PROFILE_UPDATE = "ack_profile_update"
PLAN_KIND_NONE = "none"


@dataclass(frozen=True)
class ResponsePlan:
    """A *suggestion* for the webhook to act on.

    The planner never sends messages itself — it only describes what
    *should* happen. The webhook owns the final decision and the actual
    WhatsApp send.
    """

    kind: str = PLAN_KIND_NONE
    reply_text: str | None = None
    payload: dict[str, Any] | None = None

    def is_actionable(self) -> bool:
        return self.kind != PLAN_KIND_NONE and bool(self.reply_text)


__all__ = [
    "ConversationMessage",
    "ConversationState",
    "ProfileContext",
    "ProfileUpdate",
    "NameCandidate",
    "ResponsePlan",
    "PLAN_KIND_PROFILE_ANSWER",
    "PLAN_KIND_NAME_CONFIRM",
    "PLAN_KIND_MISSING_PROFILE",
    "PLAN_KIND_ACK_PROFILE_UPDATE",
    "PLAN_KIND_NONE",
]
