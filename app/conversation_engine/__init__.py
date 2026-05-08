"""
conversation_engine — context-aware conversation layer.

Phase-9 contract
================
This package adds *memory* and *profile intelligence* on top of the
phase-3 deterministic intent layer. Pure Python; no DB / GPT / network.

Public API
----------

    schemas
        ConversationState, ConversationMessage, ProfileContext,
        ProfileUpdate, NameCandidate, ResponsePlan,
        PLAN_KIND_* constants

    conversation_state (façade over ``memory``)
        get, reset, remember_inbound, remember_outbound,
        stage_pending_name, clear_pending_name, has_pending_name,
        stage_category_hint, consume_category_hint,
        remember_uploaded_media, remember_profile_update,
        stage_pending_action

    intent_context
        ContextualIntent, INTENT_NAME_CONFIRM_YES / _NO / _RETRY,
        INTENT_PROFILE_QUESTION, resolve_intent

    profile_context
        extract_profile_update

    name_intelligence
        normalize_full_name, NAME_CONFIDENCE_THRESHOLD

    entity_protection
        ProtectionDecision, can_overwrite_name

    response_planner
        plan_for_intent, plan_for_profile_update, plan_missing_profile
"""
from __future__ import annotations

from app.conversation_engine import (
    conversation_state,
    entity_protection,
    exam_state,
    intent_context,
    memory,
    name_intelligence,
    profile_context,
    response_planner,
)
from app.conversation_engine.exam_state import (
    ExamConversationState,
    get_exam_state,
    merge_exam_state,
    record_generated_exam,
    reset_all_exam_states,
    reset_exam_state,
    set_pending_fields,
    update_last_exam_download_url,
)
from app.conversation_engine.entity_protection import (
    ProtectionDecision,
    can_overwrite_name,
)
from app.conversation_engine.intent_context import (
    INTENT_NAME_CONFIRM_NO,
    INTENT_NAME_CONFIRM_RETRY,
    INTENT_NAME_CONFIRM_YES,
    INTENT_PROFILE_QUESTION,
    ContextualIntent,
    resolve_intent,
)
from app.conversation_engine.name_intelligence import (
    NAME_CONFIDENCE_THRESHOLD,
    normalize_full_name,
)
from app.conversation_engine.profile_context import extract_profile_update
from app.conversation_engine.response_planner import (
    plan_for_intent,
    plan_for_profile_update,
    plan_missing_profile,
)
from app.conversation_engine.schemas import (
    PLAN_KIND_ACK_PROFILE_UPDATE,
    PLAN_KIND_MISSING_PROFILE,
    PLAN_KIND_NAME_CONFIRM,
    PLAN_KIND_NONE,
    PLAN_KIND_PROFILE_ANSWER,
    ConversationMessage,
    ConversationState,
    NameCandidate,
    ProfileContext,
    ProfileUpdate,
    ResponsePlan,
)

__all__ = [
    # submodules (for tests / introspection)
    "conversation_state",
    "memory",
    "intent_context",
    "profile_context",
    "name_intelligence",
    "entity_protection",
    "response_planner",
    # schemas
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
    # intent_context
    "ContextualIntent",
    "INTENT_NAME_CONFIRM_YES",
    "INTENT_NAME_CONFIRM_NO",
    "INTENT_NAME_CONFIRM_RETRY",
    "INTENT_PROFILE_QUESTION",
    "resolve_intent",
    # profile_context
    "extract_profile_update",
    # name_intelligence
    "normalize_full_name",
    "NAME_CONFIDENCE_THRESHOLD",
    # entity_protection
    "ProtectionDecision",
    "can_overwrite_name",
    # response_planner
    "plan_for_intent",
    "plan_for_profile_update",
    "plan_missing_profile",
    # exam_state (Phase-12)
    "ExamConversationState",
    "get_exam_state",
    "merge_exam_state",
    "record_generated_exam",
    "reset_all_exam_states",
    "reset_exam_state",
    "set_pending_fields",
    "update_last_exam_download_url",
]
