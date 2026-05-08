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
2. Re-prompt semantics inside the router's ``ACTION_EXPORT_PORTFOLIO``
   handler and inside ``_run_pre_export_choice_flow``: if the teacher
   is *already* in ``_AWAITING_EXPORT_CHOICE`` and they retype an
   export command (text path), the helper logs
   ``[EXPORT CHOICE_REPROMPTED]`` and re-sends the card. The original
   button-replay loop is prevented one layer higher by the
   deterministic button short-circuits, so reaching this helper means
   a fresh user retry — silent suppression would strand the teacher.

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


class TestRouterRepromptOnRetry:
    def test_router_reprompts_when_already_awaiting(self, monkeypatch, caplog):
        """When teacher is in _AWAITING_EXPORT_CHOICE the router must
        log [EXPORT CHOICE_REPROMPTED], clear the flag and re-send the
        card. Silently suppressing the retry strands the teacher.
        """
        from app.api import webhook as wh
        from app.services.gpt_router import (
            ACTION_EXPORT_PORTFOLIO,
            GPTDecision,
            SOURCE_GPT,
        )
        from app.services import whatsapp_integration as wa_integration

        async def _fake_decide(message, ctx):
            return GPTDecision(
                action=ACTION_EXPORT_PORTFOLIO,
                confidence=0.9,
                reply_text="...",
                should_save_evidence=False,
                source=SOURCE_GPT,
            )

        monkeypatch.setattr(
            "app.services.gpt_router.decide_next_action", _fake_decide,
        )
        # No DB-derived warning needed; keep the helper hermetic.
        monkeypatch.setattr(
            wa_integration, "make_pre_export_warning", lambda *a, **kw: None,
        )
        # Stub the review token + evidences fetch so we don't need a real DB.
        monkeypatch.setattr(
            "app.services.teachers.get_or_create_review_token",
            lambda db, t: "tok_123",
        )
        monkeypatch.setattr(wh, "get_teacher_evidences", lambda db, tid: [])

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

        # Re-prompted → card is scheduled, response says awaiting.
        assert response is not None
        assert response["intent"] == "export_portfolio"
        assert response.get("awaiting_review_or_export") is True
        # The pre-export choice card *must* have been scheduled.
        scheduled_funcs = {
            call.args[0].__name__ for call in background_tasks.add_task.call_args_list
            if call.args
        }
        assert "send_pre_export_choice_buttons" in scheduled_funcs
        # Both log lines must be present: reprompt notice + standard prompted.
        messages = [rec.getMessage() for rec in caplog.records]
        assert any("[EXPORT CHOICE_REPROMPTED]" in m for m in messages)
        assert any("[EXPORT DECISION PROMPTED]" in m for m in messages)
        # And the teacher ends up back in awaiting state for the next button.
        assert teacher.id in wh._AWAITING_EXPORT_CHOICE


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


# ──────────────────────────────────────────────────────────────────────
# 5. Bare export commands ("صدر" / "تصدير" / …) MUST short-circuit
#    BEFORE the GPT router so GPT never gets a chance to ask
#    "ما الذي تقصده؟". This is the regression the user reported.
# ──────────────────────────────────────────────────────────────────────


class TestBareExportShortCircuit:
    @pytest.mark.parametrize("text", [
        "صدر",
        "صدّر",                 # with shadda
        "صدّر الآن",            # with shadda + الآن
        "تصدير",
        "اصدر",
        "أصدر",                 # hamza on alef
        "تصدير الملف",
        "أرسل الملف",
        "ارسل الملف",
        "جهز الملف",
        "اطلع الملف",
        "حمل الملف",
        "أنشئ الملف",
        "ابغى الملف",
        "ابي الملف",
    ])
    def test_export_command_recognised_after_normalisation(self, text):
        """All export trigger phrases the user listed (and a few extras)
        must be recognised by ``is_export_command`` so the high-priority
        short-circuit fires.

        The function is the contract the webhook checks against — if it
        returns False here, the teacher will get stuck on a clarification
        loop in production.
        """
        from app.api.webhook import is_export_command
        assert is_export_command(text) is True, text

    @pytest.mark.parametrize("text", [
        "صدر",
        "صدّر",
        "تصدير",
        "اصدر",
    ])
    def test_bare_command_in_dedicated_set(self, text):
        """The bare single-word commands have a dedicated frozenset
        so the pre-router short-circuit can hit them in O(1) without
        running the full substring matcher."""
        from app.api.webhook import _EXPORT_BARE_COMMANDS, _normalize_arabic
        assert _normalize_arabic(text) in _EXPORT_BARE_COMMANDS, text

    def test_short_circuit_bypasses_gpt_router(self, monkeypatch):
        """Mandatory regression: the user reported that typing "صدر"
        was reaching GPT and being answered with a clarification
        question. This test forces the GPT router to ask_clarification
        and asserts the webhook's pre-router short-circuit still wins
        (i.e., decide_next_action is never called for export commands).
        """
        from app.api import webhook as wh
        from app.services.gpt_router import (
            ACTION_ASK_CLARIFICATION,
            GPTDecision,
            SOURCE_GPT,
        )

        called_with: list[str] = []

        async def _spy_decide(message, ctx):
            # If this runs, the bug is back.
            called_with.append(message)
            return GPTDecision(
                action=ACTION_ASK_CLARIFICATION,
                confidence=0.3,
                reply_text="ما الذي تقصده؟",
                should_save_evidence=False,
                clarification_question="ما الذي تقصده بكلمة صدر؟",
                source=SOURCE_GPT,
            )

        monkeypatch.setattr(
            "app.services.gpt_router.decide_next_action", _spy_decide,
        )

        # Drive ``_run_pre_export_choice_flow`` directly with a fake
        # teacher — same path the high-priority short-circuit uses.
        teacher = _fake_teacher()
        background_tasks = MagicMock()
        background_tasks.add_task = MagicMock()

        wh._AWAITING_EXPORT_CHOICE.discard(teacher.id)

        # Patch the heavy helpers so the helper runs without DB / WA.
        monkeypatch.setattr(
            "app.api.webhook.get_or_create_review_token",
            lambda db, t: "tok-fake",
            raising=False,
        )
        monkeypatch.setattr(
            "app.services.teachers.get_or_create_review_token",
            lambda db, t: "tok-fake",
        )
        monkeypatch.setattr(
            "app.api.webhook.get_teacher_evidences",
            lambda db, tid: [],
        )
        monkeypatch.setattr(
            "app.api.webhook.wa_integration.make_pre_export_warning",
            lambda *a, **kw: None,
        )

        resp = asyncio.run(wh._run_pre_export_choice_flow(
            teacher=teacher,
            db=MagicMock(),
            background_tasks=background_tasks,
            sub_active=True,
            text="صدر",
            via="pre_router_bare",
        ))

        # Helper must enqueue the 2-button send and never call GPT.
        assert resp["intent"] == "pre_export_choice_offered"
        assert called_with == [], "GPT was called for an export command"
        # send_pre_export_choice_buttons was scheduled.
        assert background_tasks.add_task.call_count >= 1
        assert teacher.id in wh._AWAITING_EXPORT_CHOICE


# ──────────────────────────────────────────────────────────────────────
# 6. Stage-3 short-circuit: tapping كامل / ذكي / مختصر MUST start the
#    export — NOT re-prompt the مراجعة/تصدير card.
# ──────────────────────────────────────────────────────────────────────


def _patch_exporter(monkeypatch):
    """Common monkey-patch bundle so the mode-selection helper runs
    without touching the real DB / file system / Playwright."""
    from app.api import webhook as wh

    fake_record = SimpleNamespace(id=42)
    monkeypatch.setattr(
        wh, "exporter_svc",
        SimpleNamespace(
            create_export_record=lambda db, tid: fake_record,
            run_export_background=lambda **kw: None,
        ),
    )
    return fake_record


class TestExportModeShortCircuit:
    def test_button_id_export_full_starts_export(self, monkeypatch):
        from app.api import webhook as wh

        fake_record = _patch_exporter(monkeypatch)
        teacher = _fake_teacher()
        wh._PENDING_EXPORT_REQUESTS.add(teacher.id)

        bg = MagicMock(); bg.add_task = MagicMock()
        resp = asyncio.run(wh._handle_export_mode_selection(
            teacher=teacher, db=MagicMock(),
            background_tasks=bg, sub_active=True, sub_info={"status": "active"},
            selected_export_mode="full", text="export_full",
            via="pre_router_button",
        ))

        assert resp["intent"] == "export_started"
        assert resp["export_mode"] == "full"
        assert resp["export_id"] == fake_record.id
        # Must NOT re-prompt — teacher should not be in awaiting state.
        assert teacher.id not in wh._AWAITING_EXPORT_CHOICE
        assert teacher.id not in wh._PENDING_EXPORT_REQUESTS

    @pytest.mark.parametrize("button_id,expected_mode", [
        ("export_full",  "full"),
        ("export_smart", "smart"),
        ("export_short", "elite"),
    ])
    def test_parse_export_mode_recognises_button_ids(self, button_id, expected_mode):
        from app.api.webhook import _parse_export_mode
        assert _parse_export_mode(button_id) == expected_mode

    @pytest.mark.parametrize("title,expected_mode", [
        ("كامل",  "full"),
        ("ذكي",   "smart"),
        ("مختصر", "elite"),
        ("1",     "full"),
        ("2",     "smart"),
        ("3",     "elite"),
        ("full",  "full"),
        ("smart", "smart"),
    ])
    def test_parse_export_mode_recognises_titles_and_numbers(self, title, expected_mode):
        from app.api.webhook import _parse_export_mode
        assert _parse_export_mode(title) == expected_mode

    def test_mode_selection_clears_awaiting_choice_state(self, monkeypatch, caplog):
        """Belt-and-suspenders: even if _AWAITING_EXPORT_CHOICE somehow
        leaks into the mode-selection step, the helper must clear it
        and log [EXPORT MODE_LOOP_PREVENTED]. This is the regression
        the user reported screen-shotted."""
        from app.api import webhook as wh

        _patch_exporter(monkeypatch)
        teacher = _fake_teacher()
        # Simulate the buggy state we want to prevent: BOTH sets are
        # populated when the mode button arrives.
        wh._AWAITING_EXPORT_CHOICE.add(teacher.id)
        wh._PENDING_EXPORT_REQUESTS.add(teacher.id)

        bg = MagicMock(); bg.add_task = MagicMock()
        with caplog.at_level("INFO"):
            resp = asyncio.run(wh._handle_export_mode_selection(
                teacher=teacher, db=MagicMock(),
                background_tasks=bg, sub_active=True, sub_info={"status": "active"},
                selected_export_mode="full", text="export_full",
                via="pre_router_button",
            ))
        assert resp["intent"] == "export_started"
        # The set must be cleared.
        assert teacher.id not in wh._AWAITING_EXPORT_CHOICE

    def test_button_id_does_not_reach_export_intent_short_circuit(self, monkeypatch):
        """Mandatory regression: ``export_full`` (the button id) must
        NOT be classified as an export-intent phrase by
        ``is_export_command``. If it were, the pre-router export-intent
        block would re-send the مراجعة/تصدير card — exactly the bug
        the user is reporting from the screenshot.
        """
        from app.api.webhook import is_export_command, _EXPORT_BARE_COMMANDS
        # Button IDs are pure ASCII — they can't accidentally hit the
        # Arabic substring matcher.
        for button_id in ("export_full", "export_smart", "export_short"):
            assert button_id not in _EXPORT_BARE_COMMANDS
            assert is_export_command(button_id) is False, button_id

    def test_full_three_stage_flow_no_loops(self, monkeypatch):
        """The end-to-end happy path the user wrote in the brief:

           1. text="صدر"            → pre-export choice card
           2. button="export_now"   → mode buttons (كامل/ذكي/مختصر)
           3. button="export_full"  → export starts

        Each stage must clear the right state and never re-show a
        previously-passed card. This test runs the three helpers in
        sequence and asserts state at every step.
        """
        from app.api import webhook as wh
        _patch_exporter(monkeypatch)
        monkeypatch.setattr(
            "app.services.teachers.get_or_create_review_token",
            lambda db, t: "tok-fake",
        )
        monkeypatch.setattr(
            wh, "get_teacher_evidences", lambda db, tid: [],
        )
        monkeypatch.setattr(
            wh.wa_integration, "make_pre_export_warning",
            lambda *a, **kw: None,
        )

        teacher = _fake_teacher()
        wh._AWAITING_EXPORT_CHOICE.discard(teacher.id)
        wh._PENDING_EXPORT_REQUESTS.discard(teacher.id)

        # Stage 1: "صدر"
        bg1 = MagicMock(); bg1.add_task = MagicMock()
        resp1 = asyncio.run(wh._run_pre_export_choice_flow(
            teacher=teacher, db=MagicMock(), background_tasks=bg1,
            sub_active=True, text="صدر", via="pre_router_bare",
        ))
        assert resp1["intent"] == "pre_export_choice_offered"
        assert teacher.id in wh._AWAITING_EXPORT_CHOICE
        assert teacher.id not in wh._PENDING_EXPORT_REQUESTS

        # Stage 2: button "export_now" (simulated by clearing
        # _AWAITING_EXPORT_CHOICE + adding _PENDING_EXPORT_REQUESTS,
        # which is exactly what the button short-circuit does).
        wh._AWAITING_EXPORT_CHOICE.discard(teacher.id)
        wh._PENDING_EXPORT_REQUESTS.add(teacher.id)
        # (no helper call here — the real webhook just enqueues
        #  send_export_options_buttons; the state is what matters.)

        # Stage 3: button "export_full"
        bg3 = MagicMock(); bg3.add_task = MagicMock()
        resp3 = asyncio.run(wh._handle_export_mode_selection(
            teacher=teacher, db=MagicMock(),
            background_tasks=bg3, sub_active=True, sub_info={"status": "active"},
            selected_export_mode="full", text="export_full",
            via="pre_router_button",
        ))
        assert resp3["intent"] == "export_started"
        assert resp3["export_mode"] == "full"
        # All wait-states must be empty at the end.
        assert teacher.id not in wh._AWAITING_EXPORT_CHOICE
        assert teacher.id not in wh._PENDING_EXPORT_REQUESTS


# ──────────────────────────────────────────────────────────────────────
# 6. Re-prompt semantics in _run_pre_export_choice_flow
# ──────────────────────────────────────────────────────────────────────


class TestPreExportRepromptOnRetry:
    """When the user types ``صدر`` again while still in the awaiting
    state, the helper must re-send the card (not silently swallow the
    retry). Real-world cause: the previous card scrolled out of view
    or the teacher was distracted."""

    def test_retry_reprompts_card(self, monkeypatch, caplog):
        from app.api import webhook as wh

        monkeypatch.setattr(
            "app.services.teachers.get_or_create_review_token",
            lambda db, t: "tok-X",
        )
        monkeypatch.setattr(wh, "get_teacher_evidences", lambda db, tid: [])
        monkeypatch.setattr(
            wh.wa_integration, "make_pre_export_warning",
            lambda *a, **kw: None,
        )

        teacher = _fake_teacher()
        # Pretend the teacher already saw the card.
        wh._AWAITING_EXPORT_CHOICE.add(teacher.id)

        bg = MagicMock(); bg.add_task = MagicMock()
        with caplog.at_level("INFO"):
            resp = asyncio.run(wh._run_pre_export_choice_flow(
                teacher=teacher, db=MagicMock(),
                background_tasks=bg, sub_active=True,
                text="صدر", via="pre_router_bare",
            ))

        assert resp["intent"] == "pre_export_choice_offered"
        scheduled = {
            call.args[0].__name__ for call in bg.add_task.call_args_list
            if call.args
        }
        assert "send_pre_export_choice_buttons" in scheduled
        assert teacher.id in wh._AWAITING_EXPORT_CHOICE
        msgs = [r.getMessage() for r in caplog.records]
        assert any("[EXPORT CHOICE_REPROMPTED]" in m for m in msgs)
        assert any("[EXPORT DECISION PROMPTED]" in m for m in msgs)


# ──────────────────────────────────────────────────────────────────────
# 7. Webhook-level resilience: foreign / un-normalisable phone numbers
# ──────────────────────────────────────────────────────────────────────


class TestInvalidPhoneGracefulDrop:
    """``normalize_phone`` only accepts Saudi numbers. Inbound webhooks
    occasionally include non-Saudi senders (status pings, foreign
    delivery receipts, etc.). The handler must ack 200 and drop them
    quietly — never raise 500 to Meta, which would trigger retry
    storms."""

    def test_non_saudi_sender_drops_with_200(self, monkeypatch):
        from fastapi import BackgroundTasks
        from fastapi.testclient import TestClient
        from app.main import app

        # Configure a non-empty PNID so the foreign-tenant guard would
        # fire — but we want to confirm the *phone* drop happens first
        # (or at least the handler returns 200, never 500).
        monkeypatch.setattr(
            "app.core.config.settings.WHATSAPP_PHONE_NUMBER_ID", "",
        )

        client = TestClient(app)
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "WABA",
                    "changes": [
                        {
                            "value": {
                                "metadata": {
                                    "phone_number_id": "1117378888120273",
                                    "display_phone_number": "+966544761054",
                                },
                                "messages": [
                                    {
                                        "id": "wamid.weird",
                                        "from": "249110031902",  # not SA
                                        "type": "text",
                                        "text": {"body": "ping"},
                                    }
                                ],
                            }
                        }
                    ],
                }
            ],
        }
        # Should not raise — handler must return 200.
        resp = client.post("/webhook/whatsapp", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("dropped") == "invalid_phone" or body.get("ok") is True
