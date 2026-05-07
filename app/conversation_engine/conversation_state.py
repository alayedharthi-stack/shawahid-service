"""
conversation_engine.conversation_state — high-level state transitions.

Thin façade on top of ``memory.py``. The webhook (and tests) talk to
this module so the underlying storage backend can change without
touching call sites.
"""
from __future__ import annotations

from app.conversation_engine import memory as _memory
from app.conversation_engine.schemas import ConversationState


def get(teacher_id: int) -> ConversationState:
    return _memory.get_state(teacher_id)


def reset(teacher_id: int) -> None:
    _memory.reset_state(teacher_id)


def remember_inbound(
    teacher_id: int,
    text: str,
    *,
    intent: str | None = None,
) -> ConversationState:
    return _memory.record_inbound(teacher_id, text, intent=intent)


def remember_outbound(
    teacher_id: int,
    text: str,
    *,
    kind: str | None = None,
) -> ConversationState:
    return _memory.record_outbound(teacher_id, text, kind=kind)


def stage_pending_name(teacher_id: int, name: str) -> ConversationState:
    return _memory.set_pending_name(teacher_id, name)


def clear_pending_name(teacher_id: int) -> ConversationState:
    return _memory.clear_pending_name(teacher_id)


def has_pending_name(teacher_id: int) -> bool:
    st = _memory.get_state(teacher_id)
    return bool(st.pending_name and st.pending_confirmation == "name")


def stage_category_hint(teacher_id: int, category: str) -> ConversationState:
    return _memory.set_category_hint(teacher_id, category)


def consume_category_hint(teacher_id: int) -> str | None:
    return _memory.consume_category_hint(teacher_id)


def remember_uploaded_media(
    teacher_id: int, evidence_type: str | None
) -> ConversationState:
    return _memory.record_uploaded_media(teacher_id, evidence_type)


def remember_profile_update(
    teacher_id: int, fields: dict[str, str] | None
) -> ConversationState:
    return _memory.record_profile_update(teacher_id, fields)


def stage_pending_action(
    teacher_id: int, action: str | None
) -> ConversationState:
    return _memory.set_pending_action(teacher_id, action)


__all__ = [
    "get",
    "reset",
    "remember_inbound",
    "remember_outbound",
    "stage_pending_name",
    "clear_pending_name",
    "has_pending_name",
    "stage_category_hint",
    "consume_category_hint",
    "remember_uploaded_media",
    "remember_profile_update",
    "stage_pending_action",
]
