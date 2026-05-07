"""
conversation_engine.memory — in-memory ConversationState store.

A *thin* per-process store keyed by ``teacher_id``. Lives in RAM so we
can iterate quickly without DB migrations during the foundation phase;
swapping to Redis later is a one-method change (``_BACKEND``).

This module is the only place that mutates state. Everything else
treats ``ConversationState`` as read-only.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

from app.conversation_engine.schemas import (
    ConversationMessage,
    ConversationState,
)

# Rolling window: keep the last N inbound/outbound turns for context.
_MAX_MESSAGES = 12

# After this much idle time we treat the teacher as a fresh session
# (still useful state, but pending confirmations expire).
_PENDING_TTL = timedelta(minutes=15)

_BACKEND: dict[int, ConversationState] = {}
_LOCK = threading.RLock()


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────


def get_state(teacher_id: int) -> ConversationState:
    """Return (and lazily create) the state for ``teacher_id``."""
    with _LOCK:
        st = _BACKEND.get(teacher_id)
        if st is None:
            st = ConversationState(teacher_id=teacher_id)
            _BACKEND[teacher_id] = st
        _expire_pending(st)
        return st


def reset_state(teacher_id: int) -> None:
    """Forget everything about ``teacher_id``. Used by tests."""
    with _LOCK:
        _BACKEND.pop(teacher_id, None)


def reset_all() -> None:
    """Wipe the whole store. Used by tests only."""
    with _LOCK:
        _BACKEND.clear()


def record_inbound(
    teacher_id: int,
    text: str,
    *,
    intent: str | None = None,
) -> ConversationState:
    """Append an inbound (teacher → bot) message and bump ``last_intent``."""
    with _LOCK:
        st = get_state(teacher_id)
        msg = ConversationMessage(text=text or "", direction="in", intent=intent)
        st.last_messages.append(msg)
        if len(st.last_messages) > _MAX_MESSAGES:
            st.last_messages = st.last_messages[-_MAX_MESSAGES:]
        if intent:
            st.last_intent = intent
        st.touch()
        return st


def record_outbound(
    teacher_id: int,
    text: str,
    *,
    kind: str | None = None,
) -> ConversationState:
    """Append an outbound (bot → teacher) message."""
    with _LOCK:
        st = get_state(teacher_id)
        msg = ConversationMessage(text=text or "", direction="out", intent=kind)
        st.last_messages.append(msg)
        if len(st.last_messages) > _MAX_MESSAGES:
            st.last_messages = st.last_messages[-_MAX_MESSAGES:]
        st.touch()
        return st


def set_pending_name(teacher_id: int, name: str | None) -> ConversationState:
    """Stage a name awaiting teacher confirmation."""
    with _LOCK:
        st = get_state(teacher_id)
        st.pending_name = name
        st.pending_confirmation = "name" if name else None
        st.touch()
        return st


def clear_pending_name(teacher_id: int) -> ConversationState:
    return set_pending_name(teacher_id, None)


def set_pending_action(teacher_id: int, action: str | None) -> ConversationState:
    """Stage an action (e.g. ``"export"``, ``"review"``) awaiting follow-up."""
    with _LOCK:
        st = get_state(teacher_id)
        st.pending_action = action
        st.touch()
        return st


def set_category_hint(teacher_id: int, category: str | None) -> ConversationState:
    with _LOCK:
        st = get_state(teacher_id)
        st.last_category_hint = category
        st.touch()
        return st


def consume_category_hint(teacher_id: int) -> str | None:
    """Return the staged category and clear it."""
    with _LOCK:
        st = get_state(teacher_id)
        cat = st.last_category_hint
        st.last_category_hint = None
        st.touch()
        return cat


def record_uploaded_media(
    teacher_id: int,
    evidence_type: str | None,
) -> ConversationState:
    with _LOCK:
        st = get_state(teacher_id)
        st.last_uploaded_media = evidence_type
        st.touch()
        return st


def record_profile_update(
    teacher_id: int,
    fields: dict[str, str] | None,
) -> ConversationState:
    with _LOCK:
        st = get_state(teacher_id)
        if fields:
            st.last_profile_update = dict(fields)
        st.touch()
        return st


# ──────────────────────────────────────────────────────────────────────
# Internals
# ──────────────────────────────────────────────────────────────────────


def _expire_pending(st: ConversationState) -> None:
    """Drop pending confirmations older than ``_PENDING_TTL``."""
    if not (st.pending_confirmation or st.pending_name or st.pending_action):
        return
    age = datetime.now(timezone.utc) - st.updated_at
    if age > _PENDING_TTL:
        st.pending_confirmation = None
        st.pending_name = None
        st.pending_action = None


__all__ = [
    "get_state",
    "reset_state",
    "reset_all",
    "record_inbound",
    "record_outbound",
    "set_pending_name",
    "clear_pending_name",
    "set_pending_action",
    "set_category_hint",
    "consume_category_hint",
    "record_uploaded_media",
    "record_profile_update",
]
