"""
tests/test_conversation_engine.py — phase-9 conversation_engine.

Covers the required scenarios from the phase brief:

    • "أنا إياد محمد الحارثي"            → ProfileUpdate.
    • Whisper noisy name                 → confirmation loop.
    • "هل تعرفني؟"                       → conversation-aware response.
    • conversation_state.last_intent     → remembered.
    • entity_protection                  → blocks silent overwrite.
    • module-level architectural rules   → no forbidden imports.
"""
from __future__ import annotations

import ast
import os

import pytest

from app.conversation_engine import (
    NAME_CONFIDENCE_THRESHOLD,
    PLAN_KIND_ACK_PROFILE_UPDATE,
    PLAN_KIND_MISSING_PROFILE,
    PLAN_KIND_NAME_CONFIRM,
    PLAN_KIND_PROFILE_ANSWER,
    ContextualIntent,
    INTENT_NAME_CONFIRM_NO,
    INTENT_NAME_CONFIRM_RETRY,
    INTENT_NAME_CONFIRM_YES,
    INTENT_PROFILE_QUESTION,
    ProfileContext,
    can_overwrite_name,
    conversation_state as cs,
    extract_profile_update,
    intent_context,
    memory,
    name_intelligence,
    normalize_full_name,
    plan_for_intent,
    plan_for_profile_update,
    plan_missing_profile,
    resolve_intent,
)


@pytest.fixture(autouse=True)
def _reset_memory():
    memory.reset_all()
    yield
    memory.reset_all()


# ──────────────────────────────────────────────────────────────────────
# 1. Profile extraction
# ──────────────────────────────────────────────────────────────────────


class TestProfileExtraction:
    def test_full_name_introduction(self):
        update = extract_profile_update("أنا إياد محمد الحارثي")
        assert "name" in update.fields
        assert "إياد" in update.fields["name"]
        assert "الحارثي" in update.fields["name"]
        assert update.confidence >= 0.5

    def test_subject_with_trigger(self):
        update = extract_profile_update("أدرس رياضيات")
        assert update.fields.get("subject") == "الرياضيات"

    def test_stage(self):
        update = extract_profile_update("أنا معلم متوسط")
        assert update.fields.get("grade") == "المرحلة المتوسطة"

    def test_school_extraction(self):
        update = extract_profile_update("مدرستي ابتدائية الفيصل")
        assert update.fields.get("school")
        assert "الفيصل" in update.fields["school"]

    def test_combined_introduction(self):
        update = extract_profile_update(
            "أنا إياد الحارثي وأدرس رياضيات في المرحلة المتوسطة"
        )
        assert "name" in update.fields
        assert update.fields.get("subject") == "الرياضيات"
        assert update.fields.get("grade") == "المرحلة المتوسطة"

    def test_empty_text(self):
        update = extract_profile_update("")
        assert update.is_empty()
        assert update.confidence == 0.0

    def test_unrelated_text(self):
        update = extract_profile_update("صباح الخير، كيف الأحوال؟")
        assert update.is_empty()


# ──────────────────────────────────────────────────────────────────────
# 2. Name intelligence
# ──────────────────────────────────────────────────────────────────────


class TestNameIntelligence:
    def test_exact_dictionary_match(self):
        candidate = normalize_full_name("إياد الحارثي")
        assert candidate.confidence >= 0.9
        assert "إياد" in candidate.normalized
        assert "الحارثي" in candidate.normalized

    def test_whisper_drift_dh_d(self):
        # "اياذ" should normalise to "اياد"
        candidate = normalize_full_name("اياذ")
        assert "اياد" in candidate.normalized or "إياد" in candidate.normalized

    def test_whisper_drift_alharthi(self):
        candidate = normalize_full_name("الحارفي")
        assert candidate.normalized == "الحارثي" or candidate.confidence >= 0.7

    def test_low_confidence_unknown_name(self):
        candidate = normalize_full_name("زززز فففف")
        assert candidate.needs_confirmation
        assert candidate.confidence < NAME_CONFIDENCE_THRESHOLD

    def test_dictionary_loaded(self):
        # Sanity: data files must exist and be non-empty.
        names = name_intelligence._given_names()
        lasts = name_intelligence._last_names()
        assert len(names) > 50
        assert len(lasts) > 30


# ──────────────────────────────────────────────────────────────────────
# 3. Confirmation loop / contextual intents
# ──────────────────────────────────────────────────────────────────────


class TestConfirmationLoop:
    def test_yes_resolves_only_when_pending(self):
        st = memory.get_state(1)
        # Without pending, "نعم" alone resolves to none (no greeting either).
        out = resolve_intent("نعم", st)
        assert out.intent != INTENT_NAME_CONFIRM_YES

    def test_yes_after_pending_name(self):
        cs.stage_pending_name(1, "إياد الحارثي")
        st = memory.get_state(1)
        out = resolve_intent("نعم اعتمده", st)
        assert out.intent == INTENT_NAME_CONFIRM_YES
        assert out.payload["name"] == "إياد الحارثي"

    def test_no_after_pending_name(self):
        cs.stage_pending_name(2, "الحارفي")
        st = memory.get_state(2)
        out = resolve_intent("لا", st)
        assert out.intent == INTENT_NAME_CONFIRM_NO

    def test_retry_audio_after_pending(self):
        cs.stage_pending_name(3, "إياد")
        st = memory.get_state(3)
        out = resolve_intent("🎤", st)
        assert out.intent == INTENT_NAME_CONFIRM_RETRY

    def test_profile_question(self):
        st = memory.get_state(4)
        out = resolve_intent("هل تعرفني؟", st)
        assert out.intent == INTENT_PROFILE_QUESTION


# ──────────────────────────────────────────────────────────────────────
# 4. Conversation memory
# ──────────────────────────────────────────────────────────────────────


class TestConversationMemory:
    def test_remembers_last_intent(self):
        cs.remember_inbound(10, "صدر الآن", intent="export")
        st = memory.get_state(10)
        assert st.last_intent == "export"
        assert len(st.last_messages) == 1

    def test_rolling_window_caps(self):
        for i in range(20):
            cs.remember_inbound(11, f"رسالة {i}")
        st = memory.get_state(11)
        assert len(st.last_messages) <= memory._MAX_MESSAGES

    def test_pending_name_lifecycle(self):
        cs.stage_pending_name(12, "الحارثي")
        assert cs.has_pending_name(12)
        cs.clear_pending_name(12)
        assert not cs.has_pending_name(12)

    def test_category_hint_consume_clears(self):
        cs.stage_category_hint(13, "التخطيط")
        assert cs.consume_category_hint(13) == "التخطيط"
        assert cs.consume_category_hint(13) is None

    def test_reset_state(self):
        cs.remember_inbound(14, "مرحبا")
        cs.reset(14)
        st = memory.get_state(14)
        assert st.last_intent is None
        assert st.last_messages == []


# ──────────────────────────────────────────────────────────────────────
# 5. Entity protection
# ──────────────────────────────────────────────────────────────────────


class TestEntityProtection:
    def test_no_current_allows(self):
        d = can_overwrite_name(current=None, proposed="إياد")
        assert d.allow

    def test_equal_names_allow(self):
        d = can_overwrite_name(current="إياد الحارثي", proposed="اياد الحارثي")
        assert d.allow

    def test_different_names_blocked(self):
        d = can_overwrite_name(current="إياد الحارثي", proposed="عبدالله القحطاني")
        assert not d.allow
        assert d.needs_confirmation

    def test_fuzzy_variant_blocked(self):
        # Whisper drift — almost the same, still must confirm.
        d = can_overwrite_name(current="الحارثي", proposed="الحارفي")
        assert not d.allow
        assert d.needs_confirmation

    def test_unconfirmed_existing_allows(self):
        d = can_overwrite_name(
            current="مؤقت", proposed="إياد الحارثي", confirmed=False
        )
        assert d.allow


# ──────────────────────────────────────────────────────────────────────
# 6. Response planner
# ──────────────────────────────────────────────────────────────────────


class TestResponsePlanner:
    def test_who_am_i_with_full_profile(self):
        profile = ProfileContext(
            teacher_name="إياد محمد الحارثي",
            subject="الرياضيات",
            grades=("المرحلة المتوسطة",),
        )
        intent = ContextualIntent(INTENT_PROFILE_QUESTION, 0.9, source="context")
        plan = plan_for_intent(intent, memory.get_state(1), profile)
        assert plan.kind == PLAN_KIND_PROFILE_ANSWER
        assert "إياد محمد الحارثي" in plan.reply_text
        assert "الرياضيات" in plan.reply_text

    def test_who_am_i_when_empty(self):
        profile = ProfileContext()
        intent = ContextualIntent(INTENT_PROFILE_QUESTION, 0.9, source="context")
        plan = plan_for_intent(intent, memory.get_state(1), profile)
        assert plan.kind == PLAN_KIND_PROFILE_ANSWER
        assert "لا أعرف" in plan.reply_text or "أرسل" in plan.reply_text

    def test_low_confidence_name_triggers_confirm(self):
        # Use a non-dictionary name so name_intelligence returns low confidence.
        from app.conversation_engine.schemas import ProfileUpdate
        update = ProfileUpdate(fields={"name": "زززز ققققق"}, confidence=0.5)
        plan = plan_for_profile_update(update, memory.get_state(1), ProfileContext())
        assert plan.kind == PLAN_KIND_NAME_CONFIRM
        assert "نعم" in plan.reply_text

    def test_missing_profile_only_asks_for_missing(self):
        profile = ProfileContext(teacher_name="إياد")
        plan = plan_missing_profile(profile)
        assert plan.kind == PLAN_KIND_MISSING_PROFILE
        assert "اسمك" not in plan.reply_text  # already known
        assert "المادة" in plan.reply_text


# ──────────────────────────────────────────────────────────────────────
# 7. Architectural contracts
# ──────────────────────────────────────────────────────────────────────

_FORBIDDEN_PREFIXES = (
    "app.export_engine",
    "app.media_engine",
    "app.review_engine",
    "app.storage_engine",
    "playwright",
    "openai",
    "sqlalchemy",
)


def _walk_imports(path: str):
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                yield node.module


class TestArchitecturalContracts:
    def test_conversation_engine_has_no_forbidden_imports(self):
        pkg_root = os.path.dirname(memory.__file__)
        for fname in os.listdir(pkg_root):
            if not fname.endswith(".py"):
                continue
            full = os.path.join(pkg_root, fname)
            for module in _walk_imports(full):
                for forbidden in _FORBIDDEN_PREFIXES:
                    assert not module.startswith(forbidden), (
                        f"{fname} imports forbidden module {module}"
                    )

    def test_intent_context_uses_base_intents(self):
        # Sanity: still delegates to detect_intent for non-special cases.
        st = memory.get_state(99)
        out = resolve_intent("صدر الآن", st)
        assert out.intent == "export"
        assert out.source == "base"
