"""
tests/test_gpt_router.py — Phase-13 GPT-First Router.

Required scenarios from the brief:

    • "أريد اختبار رياضيات" → action=create_exam, no Evidence
    • "اختبار بسيط" follow-up → still create_exam (in_exam_flow context)
    • "من أنت؟" → chat_reply, no Evidence
    • "اسمي إياد" → update_profile, no Evidence
    • voice asking for exam → create_exam, no Evidence
    • low confidence → ask_clarification (GPT decides), no Evidence
    • GPT failure → fallback router still produces a sane decision
    • should_save_evidence is True ONLY for action == save_evidence
    • invalid action from GPT → coerced to "unknown"
    • profile_update / exam_request payloads are propagated cleanly
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import pytest

from app.services.gpt_router import (
    ACTION_ASK_CLARIFICATION,
    ACTION_CHAT_REPLY,
    ACTION_CREATE_EXAM,
    ACTION_DELETE_OR_EDIT,
    ACTION_EXPORT_PORTFOLIO,
    ACTION_REVIEW_PORTFOLIO,
    ACTION_SAVE_EVIDENCE,
    ACTION_UNKNOWN,
    ACTION_UPDATE_PROFILE,
    SOURCE_DISABLED,
    SOURCE_FALLBACK,
    SOURCE_GPT,
    GPTDecision,
    RouterContext,
    VALID_ACTIONS,
    decide_next_action,
)


# ──────────────────────────────────────────────────────────────────────
# Mock OpenAI client builder
# ──────────────────────────────────────────────────────────────────────


@dataclass
class _FakeMessage:
    content: str


@dataclass
class _FakeChoice:
    message: _FakeMessage


@dataclass
class _FakeResponse:
    choices: list[_FakeChoice]


class _FakeChatCompletions:
    """Stand-in for ``client.chat.completions``."""

    def __init__(self, payload: dict[str, Any] | str | Exception) -> None:
        self._payload = payload
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        if isinstance(self._payload, Exception):
            raise self._payload
        body = (
            self._payload
            if isinstance(self._payload, str)
            else json.dumps(self._payload, ensure_ascii=False)
        )
        return _FakeResponse(choices=[_FakeChoice(message=_FakeMessage(content=body))])


class _FakeChat:
    def __init__(self, completions: _FakeChatCompletions) -> None:
        self.completions = completions


class _FakeOpenAI:
    """Minimal stand-in for ``openai.AsyncOpenAI`` used by the router."""

    def __init__(self, payload: dict[str, Any] | str | Exception) -> None:
        self.completions = _FakeChatCompletions(payload)
        self.chat = _FakeChat(self.completions)


def _client(payload: dict[str, Any] | str | Exception) -> _FakeOpenAI:
    return _FakeOpenAI(payload)


def _decide(message: str, ctx: RouterContext, *, openai_client: Any) -> GPTDecision:
    """Sync wrapper for the async router — keeps tests pytest-asyncio-free."""
    return asyncio.run(decide_next_action(message, ctx, openai_client=openai_client))


# ──────────────────────────────────────────────────────────────────────
# Action-by-action GPT decisions
# ──────────────────────────────────────────────────────────────────────


def test_create_exam_decision_does_not_save_evidence():
    payload = {
        "action": "create_exam",
        "confidence": 0.92,
        "reply_text": "تمام، سأجهّز لك الاختبار 📘",
        "should_save_evidence": True,  # must be ignored — wrong action
        "exam_request": {
            "subject": "الرياضيات",
            "grade": "الصف الرابع",
            "exam_type": "اختبار نهائي",
        },
    }
    decision = _decide(
        "أريد اختبار رياضيات للصف الرابع نهائي",
        RouterContext(teacher_id=1),
        openai_client=_client(payload),
    )

    assert decision.action == ACTION_CREATE_EXAM
    assert decision.should_save_evidence is False  # only save_evidence may save
    assert decision.exam_request is not None
    assert decision.exam_request["subject"] == "الرياضيات"
    assert decision.source == SOURCE_GPT


def test_chat_reply_decision_never_saves():
    payload = {
        "action": "chat_reply",
        "confidence": 0.95,
        "reply_text": "أنا شواهد AI 🌿 أساعدك في توثيق شواهدك.",
        "should_save_evidence": True,  # must be coerced to False
    }
    decision = _decide(
        "من أنت؟",
        RouterContext(teacher_id=1),
        openai_client=_client(payload),
    )

    assert decision.action == ACTION_CHAT_REPLY
    assert decision.should_save_evidence is False
    assert decision.is_pure_reply is True
    assert "شواهد AI" in decision.reply_text


def test_update_profile_decision_carries_payload():
    payload = {
        "action": "update_profile",
        "confidence": 0.9,
        "reply_text": "تشرفت يا أستاذ إياد ✅",
        "should_save_evidence": False,
        "profile_update": {"name": "إياد محمد الحارثي"},
    }
    decision = _decide(
        "اسمي إياد محمد الحارثي",
        RouterContext(teacher_id=1),
        openai_client=_client(payload),
    )

    assert decision.action == ACTION_UPDATE_PROFILE
    assert decision.should_save_evidence is False
    assert decision.profile_update == {"name": "إياد محمد الحارثي"}


def test_save_evidence_is_only_action_that_persists():
    payload = {
        "action": "save_evidence",
        "confidence": 0.88,
        "reply_text": "تم حفظ الشاهد ✅",
        "should_save_evidence": True,
        "evidence_hint": {
            "category": "نشاط صفي",
            "title": "نشاط جماعي داخل الصف",
            "description": "نشاط تعاوني للطلاب في حصة الرياضيات.",
        },
    }
    decision = _decide(
        "هذه صورة نشاط جماعي داخل الصف",
        RouterContext(teacher_id=1, has_media=True, media_type="image"),
        openai_client=_client(payload),
    )

    assert decision.action == ACTION_SAVE_EVIDENCE
    assert decision.should_save_evidence is True
    assert decision.is_save is True
    assert decision.evidence_hint is not None
    assert decision.evidence_hint["category"] == "نشاط صفي"


def test_export_portfolio_action():
    payload = {
        "action": "export_portfolio",
        "confidence": 0.97,
        "reply_text": "ممتاز، سأجهّز لك خيارات التصدير 📘",
        "should_save_evidence": False,
        "export_request": {"mode": "smart"},
    }
    decision = _decide(
        "صدر ملفي",
        RouterContext(teacher_id=1),
        openai_client=_client(payload),
    )

    assert decision.action == ACTION_EXPORT_PORTFOLIO
    assert decision.should_save_evidence is False
    assert decision.export_request == {"mode": "smart"}


def test_review_portfolio_action():
    payload = {
        "action": "review_portfolio",
        "confidence": 0.93,
        "reply_text": "أرسلت لك رابط المراجعة ✏️",
        "should_save_evidence": False,
    }
    decision = _decide(
        "راجع ملفي قبل التصدير",
        RouterContext(teacher_id=1),
        openai_client=_client(payload),
    )

    assert decision.action == ACTION_REVIEW_PORTFOLIO
    assert decision.should_save_evidence is False


def test_delete_or_edit_action_is_pure_reply():
    payload = {
        "action": "delete_or_edit",
        "confidence": 0.87,
        "reply_text": "يمكنك التعديل من رابط المراجعة ✏️",
        "should_save_evidence": False,
    }
    decision = _decide(
        "احذف آخر شاهد",
        RouterContext(teacher_id=1),
        openai_client=_client(payload),
    )

    assert decision.action == ACTION_DELETE_OR_EDIT
    assert decision.is_pure_reply is True
    assert decision.should_save_evidence is False


def test_ask_clarification_when_meaning_is_vague():
    payload = {
        "action": "ask_clarification",
        "confidence": 0.4,
        "reply_text": "هل تقصد إنشاء اختبار جديد أم مراجعة ملفك؟",
        "should_save_evidence": False,
        "needs_clarification": True,
        "clarification_question": "هل تقصد إنشاء اختبار جديد أم مراجعة ملفك؟",
    }
    decision = _decide(
        "ابغى",
        RouterContext(teacher_id=1),
        openai_client=_client(payload),
    )

    assert decision.action == ACTION_ASK_CLARIFICATION
    assert decision.should_save_evidence is False
    assert decision.needs_clarification is True
    assert decision.clarification_question


# ──────────────────────────────────────────────────────────────────────
# Hard invariants
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "action",
    sorted(VALID_ACTIONS - {ACTION_SAVE_EVIDENCE}),
)
def test_should_save_evidence_only_when_action_is_save_evidence(action: str):
    """Even if GPT returns should_save_evidence=true with the wrong
    action, the coercer must zero it out."""
    payload = {
        "action": action,
        "confidence": 0.9,
        "reply_text": "...",
        "should_save_evidence": True,
    }
    decision = _decide(
        "أي رسالة",
        RouterContext(teacher_id=1),
        openai_client=_client(payload),
    )
    assert decision.action == action
    assert decision.should_save_evidence is False, (
        f"action={action} must not allow should_save_evidence=true"
    )


def test_invalid_action_is_coerced_to_unknown():
    payload = {
        "action": "do_something_weird",
        "confidence": 0.9,
        "reply_text": "...",
        "should_save_evidence": False,
    }
    decision = _decide(
        "نص ما",
        RouterContext(teacher_id=1),
        openai_client=_client(payload),
    )
    assert decision.action == ACTION_UNKNOWN


def test_confidence_clamped_to_unit_interval():
    payload = {
        "action": "chat_reply",
        "confidence": 7.5,            # out of bounds
        "reply_text": "تمام",
        "should_save_evidence": False,
    }
    decision = _decide(
        "مرحبًا",
        RouterContext(teacher_id=1),
        openai_client=_client(payload),
    )
    assert 0.0 <= decision.confidence <= 1.0


# ──────────────────────────────────────────────────────────────────────
# Empty / missing input
# ──────────────────────────────────────────────────────────────────────


def test_empty_message_returns_unknown_without_calling_gpt():
    fake = _client({"action": "chat_reply"})  # would explode if called
    decision = _decide("", RouterContext(teacher_id=1), openai_client=fake)
    assert decision.action == ACTION_UNKNOWN
    assert decision.should_save_evidence is False
    assert fake.completions.calls == []   # GPT never called


# ──────────────────────────────────────────────────────────────────────
# GPT failure → deterministic fallback
# ──────────────────────────────────────────────────────────────────────


def test_gpt_exception_falls_back_to_intents_resolver():
    decision = _decide(
        "أريد اختبار رياضيات للصف الرابع",
        RouterContext(teacher_id=1),
        openai_client=_client(RuntimeError("boom")),
    )
    # Even when GPT fails, fallback maps INTENT_CREATE_EXAM → create_exam
    # and never sets should_save_evidence to True.
    assert decision.action == ACTION_CREATE_EXAM
    assert decision.should_save_evidence is False
    assert decision.source == SOURCE_FALLBACK


def test_invalid_json_falls_back():
    decision = _decide(
        "صدر ملفي",
        RouterContext(teacher_id=1),
        openai_client=_client("not a json"),
    )
    assert decision.source == SOURCE_FALLBACK
    assert decision.action in {ACTION_EXPORT_PORTFOLIO, ACTION_UNKNOWN}
    assert decision.should_save_evidence is False


def test_disabled_when_openai_key_missing(monkeypatch):
    """Without OPENAI_API_KEY the router still returns a sane decision."""
    monkeypatch.setattr("app.services.gpt_router.settings.OPENAI_API_KEY", "", raising=False)
    decision = asyncio.run(decide_next_action(
        "راجع ملفي",
        RouterContext(teacher_id=1),
        openai_client=None,   # let it pick the default path → SOURCE_DISABLED
    ))
    assert decision.source == SOURCE_DISABLED
    assert decision.should_save_evidence is False


# ──────────────────────────────────────────────────────────────────────
# Context propagation
# ──────────────────────────────────────────────────────────────────────


def test_context_is_serialised_into_user_payload():
    """The teacher's context must reach GPT so it can disambiguate
    follow-up messages like "اختبار قصير" while in an exam flow."""
    fake = _client({
        "action": "create_exam",
        "confidence": 0.9,
        "reply_text": "تمام",
        "should_save_evidence": False,
    })
    _decide(
        "اختبار قصير",
        RouterContext(
            teacher_id=42,
            teacher_name="إياد",
            teacher_subject="الرياضيات",
            in_exam_flow=True,
        ),
        openai_client=fake,
    )
    assert fake.completions.calls, "GPT must have been called"
    user_msg = fake.completions.calls[0]["messages"][1]["content"]
    assert "إياد" in user_msg
    assert "الرياضيات" in user_msg
    assert "تدفق إنشاء اختبار" in user_msg


def test_voice_transcript_context_flag_visible_to_gpt():
    fake = _client({
        "action": "update_profile",
        "confidence": 0.9,
        "reply_text": "تم تحديث اسمك ✅",
        "should_save_evidence": False,
        "profile_update": {"name": "إياد"},
    })
    _decide(
        "اسمي إياد",
        RouterContext(teacher_id=1, has_media=True, media_type="audio", has_transcript=True),
        openai_client=fake,
    )
    user_msg = fake.completions.calls[0]["messages"][1]["content"]
    assert "تفريغ صوتي" in user_msg


# ──────────────────────────────────────────────────────────────────────
# Datatype hygiene
# ──────────────────────────────────────────────────────────────────────


def test_decision_to_dict_excludes_raw():
    d = GPTDecision(action=ACTION_CHAT_REPLY, raw={"some": "data"})
    out = d.to_dict()
    assert "raw" not in out
    assert out["action"] == ACTION_CHAT_REPLY


def test_is_save_requires_both_action_and_flag():
    # action correct but flag false
    d1 = GPTDecision(action=ACTION_SAVE_EVIDENCE, should_save_evidence=False)
    assert d1.is_save is False
    # flag true but wrong action
    d2 = GPTDecision(action=ACTION_CHAT_REPLY, should_save_evidence=True)
    assert d2.is_save is False
    # both
    d3 = GPTDecision(action=ACTION_SAVE_EVIDENCE, should_save_evidence=True)
    assert d3.is_save is True


def test_pure_reply_actions():
    for action in (ACTION_CHAT_REPLY, ACTION_ASK_CLARIFICATION, ACTION_DELETE_OR_EDIT):
        assert GPTDecision(action=action).is_pure_reply is True
    for action in (ACTION_CREATE_EXAM, ACTION_SAVE_EVIDENCE,
                   ACTION_UPDATE_PROFILE, ACTION_EXPORT_PORTFOLIO,
                   ACTION_REVIEW_PORTFOLIO, ACTION_UNKNOWN):
        assert GPTDecision(action=action).is_pure_reply is False
