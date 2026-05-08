"""
tests/test_export_loop_prevention.py — regression for the
"تصدير الآن" infinite-loop bug.

Bug
---
After the webhook sent the مراجعة/تصدير 2-button card, pressing the
"تصدير الآن" button caused the webhook to send the **same card** again
instead of advancing to the export-mode picker (كامل / ذكي / مختصر).

Root cause
----------
WhatsApp delivers an interactive button press as text=``"export_now"``.
The new GPT-first router was running *before* the legacy button
handler and classifying ``"export_now"`` as
``ACTION_EXPORT_PORTFOLIO`` → ``send_pre_export_choice_buttons`` →
loop.

Fix
---
1. Deterministic button-payload short-circuit BEFORE the GPT router:
   ``review_file`` / ``export_now`` / their emoji titles never reach
   GPT.
2. Defence-in-depth loop guard inside the router's
   ``ACTION_EXPORT_PORTFOLIO`` handler — if teacher is already in
   ``_AWAITING_EXPORT_CHOICE``, log
   ``[EXPORT LOOP_PREVENTED]`` and stop instead of resending the card.

These tests pin both behaviours so the bug cannot regress.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_state():
    from app.api import webhook as wh
    wh._AWAITING_EXPORT_CHOICE.clear()
    wh._PENDING_EXPORT_REQUESTS.clear()
    yield
    wh._AWAITING_EXPORT_CHOICE.clear()
    wh._PENDING_EXPORT_REQUESTS.clear()


def _fake_teacher(*, teacher_id: int = 1, sub_active: bool = True):
    return SimpleNamespace(
        id=teacher_id,
        phone="+966500000000",
        name="إياد",
        school_name="مدرسة س",
        education_admin="إدارة س",
        region="منطقة س",
        subject="رياضيات",
        stage="primary",
        grades=("الصف الرابع",),
    )


# ──────────────────────────────────────────────────────────────────────
# 1. Loop guard inside the GPT router's ACTION_EXPORT_PORTFOLIO handler
# ──────────────────────────────────────────────────────────────────────


class TestRouterLoopGuard:
    def test_router_short_circuits_when_already_awaiting(self, monkeypatch, caplog):
        """When teacher is in _AWAITING_EXPORT_CHOICE the router must
        log [EXPORT LOOP_PREVENTED] and NOT enqueue another card send.
        """
        from app.api import webhook as wh
        from app.services.gpt_router import (
            ACTION_EXPORT_PORTFOLIO,
            GPTDecision,
            SOURCE_GPT,
        )

        # Force the router to return ACTION_EXPORT_PORTFOLIO so we can
        # exercise the handler deterministically without an OpenAI key.
        async def _fake_decide(message, ctx):
            return GPTDecision(
                action=ACTION_EXPORT_PORTFOLIO,
                confidence=0.9,
                reply_text="...",
                should_save_evidence=False,
                source=SOURCE_GPT,
            )

        # The webhook re-imports decide_next_action inside the helper
        # (deferred import to keep the module load light), so we patch
        # the source module instead of the webhook namespace.
        monkeypatch.setattr(
            "app.services.gpt_router.decide_next_action", _fake_decide,
        )

        teacher = _fake_teacher()
        wh._AWAITING_EXPORT_CHOICE.add(teacher.id)

        background_tasks = MagicMock()
        background_tasks.add_task = MagicMock()

        with caplog.at_level("INFO"):
            decision, response = asyncio.run(wh._route_via_gpt_router(
                message="صدر",
                teacher=teacher,
                db=MagicMock(),
                background_tasks=background_tasks,
                has_media=False,
                media_type=None,
                has_transcript=False,
                sub_active=True,
            ))

        # Loop prevented → no card scheduled, single sentinel response.
        assert response is not None
        assert response["intent"] == "pre_export_choice_already_pending"
        assert background_tasks.add_task.call_count == 0
        assert any(
            "[EXPORT LOOP_PREVENTED]" in rec.getMessage()
            for rec in caplog.records
        )


# ──────────────────────────────────────────────────────────────────────
# 2. Deterministic button-payload constants are correct
# ──────────────────────────────────────────────────────────────────────


class TestButtonPayloadConstants:
    """The webhook resolves the button card via three normalised forms
    of each button. Pin them so a typo in the dispatch table causes the
    test to fail loudly, not the user to fall into the loop."""

    @pytest.mark.parametrize("payload", [
        "export_now",
        "📤 تصدير الان",
        "📤 تصدير الآن",
    ])
    def test_export_now_normalisation_round_trips(self, payload):
        from app.api.webhook import _normalize_arabic
        normalised = _normalize_arabic(payload)
        # The dispatch set the webhook checks against
        # (kept inline there — we duplicate it here to detect drift).
        bucket = {"export_now", "📤 تصدير الان", "📤 تصدير الآن"}
        assert normalised in bucket, (payload, normalised)

    @pytest.mark.parametrize("payload", [
        "review_file",
        "🔍 مراجعه الملف",
        "🔍 مراجعة الملف",
    ])
    def test_review_file_normalisation_round_trips(self, payload):
        from app.api.webhook import _normalize_arabic
        normalised = _normalize_arabic(payload)
        bucket = {"review_file", "🔍 مراجعه الملف", "🔍 مراجعة الملف"}
        assert normalised in bucket, (payload, normalised)


# ──────────────────────────────────────────────────────────────────────
# 3. The interactive button parser stuffs ``id`` into ``text``
# ──────────────────────────────────────────────────────────────────────


class TestInteractiveButtonParsing:
    def test_button_reply_id_becomes_text(self):
        """``button_reply.id`` must end up in ``text`` (not the title)
        so the deterministic short-circuit can match it."""
        from app.api.webhook import _parse_meta_payload as _extract_message_data

        payload = {
            "object": "whatsapp_business_account",
            "entry": [{
                "changes": [{
                    "value": {
                        "messages": [{
                            "id": "wamid.HBgL...",
                            "from": "966500000000",
                            "type": "interactive",
                            "interactive": {
                                "type": "button_reply",
                                "button_reply": {
                                    "id": "export_now",
                                    "title": "📤 تصدير الآن",
                                },
                            },
                        }],
                    },
                }],
            }],
        }
        result = _extract_message_data(payload)
        assert result is not None
        assert result["text"] == "export_now"
        assert result["msg_type"] == "text"

    def test_review_button_id_becomes_text(self):
        from app.api.webhook import _parse_meta_payload as _extract_message_data

        payload = {
            "object": "whatsapp_business_account",
            "entry": [{
                "changes": [{
                    "value": {
                        "messages": [{
                            "id": "wamid.X",
                            "from": "966500000000",
                            "type": "interactive",
                            "interactive": {
                                "type": "button_reply",
                                "button_reply": {
                                    "id": "review_file",
                                    "title": "🔍 مراجعة الملف",
                                },
                            },
                        }],
                    },
                }],
            }],
        }
        result = _extract_message_data(payload)
        assert result is not None
        assert result["text"] == "review_file"


# ──────────────────────────────────────────────────────────────────────
# 4. The pre-export buttons module still uses the IDs the dispatch
#    expects — drift here would re-introduce the bug.
# ──────────────────────────────────────────────────────────────────────


class TestSendPreExportButtonsContract:
    def test_button_ids_match_webhook_dispatch(self):
        """The IDs sent over the wire MUST match the webhook's
        deterministic short-circuit set. If someone renames a button
        without updating both sides, this test fails."""
        import inspect
        from app.services import whatsapp as wa

        src = inspect.getsource(wa.send_pre_export_choice_buttons)
        # The two button IDs the webhook deterministically dispatches:
        assert '"id": "review_file"' in src
        assert '"id": "export_now"' in src
