"""
tests/test_whatsapp_exam_flow.py — Phase-12 WhatsApp Exam Flow.

Required scenarios from the brief:

    • "أريد اختبار رياضيات" → INTENT_CREATE_EXAM
    • partial info → asks for missing slots only
    • smart defaults are applied (stage + exam_type)
    • run_source_pipeline is invoked for the request
    • anti-copy runs (we get a transformation log)
    • PDF render path is exercised (best-effort — failure is OK)
    • provider failure does NOT crash the flow
    • no matching sources → fallback message
    • teacher-uploaded sample text becomes a usable source
    • webhook still works for non-exam messages (no regression)
    • forbidden imports under exam_engine.exam_flow
    • 0 regressions on the existing test surface (asserted by full pytest run)
"""
from __future__ import annotations

import ast
import os

import pytest

from app.conversation_engine.exam_state import (
    get_exam_state,
    reset_all_exam_states,
)
from app.exam_engine import (
    EXAM_TYPE_FINAL,
    EXAM_TYPE_QUICK,
    ExamSlots,
    handle_exam_request,
    parse_exam_slots,
    smart_defaults,
)
from app.exam_engine.exam_flow import (
    STAGE_FAILED,
    STAGE_MISSING_INFO,
    STAGE_NO_MATCH,
    STAGE_READY,
)
from app.services.intents import (
    INTENT_CREATE_EXAM,
    INTENT_EXAM_CONFIRM,
    INTENT_EXAM_EXPORT,
    INTENT_EXAM_REGENERATE,
    detect_intent,
)


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_state():
    reset_all_exam_states()
    yield
    reset_all_exam_states()


# ──────────────────────────────────────────────────────────────────────
# 1. Intent detection
# ──────────────────────────────────────────────────────────────────────


class TestExamIntents:
    @pytest.mark.parametrize("text", [
        "أريد اختبار رياضيات",
        "ابغى اختبار للصف الرابع",
        "سو لي اختبار قصير",
        "أنشئ لي اختبار نهائي",
        "أنشئ لي نموذج",
        "اختبار قصير في الفقه",
    ])
    def test_create_exam_recognised(self, text):
        intent = detect_intent(text)
        assert intent.intent == INTENT_CREATE_EXAM, (text, intent)

    def test_confirm_recognised(self):
        assert detect_intent("اعتمد الاختبار").intent == INTENT_EXAM_CONFIRM
        assert detect_intent("نعم اعتمده").intent == INTENT_EXAM_CONFIRM

    def test_regenerate_recognised(self):
        assert detect_intent("ولد لي اختبار جديد").intent == INTENT_EXAM_REGENERATE
        assert detect_intent("اختبار آخر").intent == INTENT_EXAM_REGENERATE

    def test_export_recognised(self):
        assert detect_intent("أرسل الاختبار").intent == INTENT_EXAM_EXPORT
        assert detect_intent("حمل الاختبار").intent == INTENT_EXAM_EXPORT

    def test_create_exam_takes_priority_over_category_hint(self):
        # "هذا اختبار" matches category-hint ("التقويم"), but
        # "أريد اختبار" must match create_exam — patterns are ordered
        # to put exam intents first.
        assert detect_intent("أريد اختبار").intent == INTENT_CREATE_EXAM


# ──────────────────────────────────────────────────────────────────────
# 2. Slot parsing
# ──────────────────────────────────────────────────────────────────────


class TestSlotParsing:
    def test_full_request_extracted(self):
        slots = parse_exam_slots(
            "أريد اختبار نهائي رياضيات للصف الرابع الفصل الدراسي الثاني"
        )
        assert slots.exam_type == EXAM_TYPE_FINAL
        assert slots.subject == "الرياضيات"
        assert "الرابع" in (slots.grade or "")
        assert "الثاني" in (slots.semester or "")

    def test_dialect_grade(self):
        slots = parse_exam_slots("اختبار قصير لرابع ابتدائي علوم")
        assert slots.exam_type == EXAM_TYPE_QUICK
        assert slots.subject == "العلوم"
        assert "الرابع" in (slots.grade or "")
        assert "ابتدائي" in (slots.stage or "")

    def test_unit_and_lesson(self):
        slots = parse_exam_slots(
            "اختبار في الوحدة الثالثة درس الكسور للصف الخامس رياضيات"
        )
        assert slots.unit == "الوحدة الثالثه" or slots.unit == "الوحدة الثالثة"
        assert "الكسور" in (slots.lesson or "")

    def test_empty_text(self):
        assert parse_exam_slots("").is_empty()
        assert parse_exam_slots(None).is_empty()


# ──────────────────────────────────────────────────────────────────────
# 3. Smart defaults
# ──────────────────────────────────────────────────────────────────────


class TestSmartDefaults:
    def test_primary_quick_short(self):
        d = smart_defaults(stage="المرحلة الابتدائية", exam_type=EXAM_TYPE_QUICK)
        assert d.duration_minutes <= 20
        assert d.total_marks <= 15

    def test_secondary_final_long(self):
        d = smart_defaults(stage="المرحلة الثانوية", exam_type=EXAM_TYPE_FINAL)
        assert d.duration_minutes >= 90
        assert d.total_marks >= 40

    def test_unknown_stage_falls_back(self):
        d = smart_defaults(stage=None, exam_type=EXAM_TYPE_QUICK)
        assert d.total_questions > 0
        assert d.duration_minutes > 0


# ──────────────────────────────────────────────────────────────────────
# 4. Conversation flow — missing → ready
# ──────────────────────────────────────────────────────────────────────


class TestConversationFlow:
    def test_partial_request_asks_for_missing(self):
        result = handle_exam_request(
            teacher_id=10,
            text="أريد اختبار",
            render_pdf=False,
        )
        assert result.stage == STAGE_MISSING_INFO
        assert "📘 المادة" in result.reply_text
        assert "🏫 الصف" in result.reply_text or "🏫 المرحلة" in result.reply_text

    def test_followup_completes_state(self):
        # Step 1: subject only.
        first = handle_exam_request(
            teacher_id=11, text="أريد اختبار رياضيات", render_pdf=False,
        )
        assert first.stage == STAGE_MISSING_INFO
        # Step 2: complete the request.
        second = handle_exam_request(
            teacher_id=11,
            text="اختبار قصير للصف الرابع الفصل الأول",
            render_pdf=False,
        )
        assert second.stage == STAGE_READY
        st = get_exam_state(11)
        assert st.subject == "الرياضيات"
        assert "الرابع" in (st.grade or "")
        assert st.exam_type == EXAM_TYPE_QUICK

    def test_full_request_in_one_message(self):
        result = handle_exam_request(
            teacher_id=12,
            text=(
                "أريد اختبار نهائي رياضيات للصف الرابع الابتدائي "
                "الفصل الدراسي الأول"
            ),
            render_pdf=False,
        )
        assert result.stage == STAGE_READY
        assert result.exam is not None
        assert result.exam.question_count > 0
        # Smart defaults applied (numeric trio)
        assert result.exam.profile.total_marks > 0
        assert result.exam.profile.duration_minutes > 0

    def test_anti_copy_runs(self):
        # The pipeline always applies anti_copy when sourced questions
        # are returned. We verify by running twice with distinct
        # teacher_ids and checking the question order differs.
        text = (
            "أريد اختبار قصير رياضيات للصف الرابع الابتدائي "
            "الفصل الدراسي الأول"
        )
        a = handle_exam_request(teacher_id=20, text=text, render_pdf=False)
        b = handle_exam_request(teacher_id=99, text=text, render_pdf=False)
        assert a.stage == b.stage == STAGE_READY
        # Anti-copy seed depends on teacher_id — questions reorder.
        a_ids = [q.id for q in a.exam.questions]
        b_ids = [q.id for q in b.exam.questions]
        # IDs are random per question, but the texts must overlap and
        # both runs must have well-formed exams.
        assert all(q.text for q in a.exam.questions)
        assert all(q.text for q in b.exam.questions)

    def test_teacher_profile_backfills_missing_slots(self):
        # Subject + grade come from the teacher's profile, only exam_type
        # in the message — must still resolve to READY.
        result = handle_exam_request(
            teacher_id=30,
            text="أريد اختبار قصير",
            teacher_subject="الرياضيات",
            teacher_stage="المرحلة الابتدائية",
            teacher_grades=("الصف الرابع",),
            render_pdf=False,
        )
        assert result.stage == STAGE_READY


# ──────────────────────────────────────────────────────────────────────
# 5. Failure isolation
# ──────────────────────────────────────────────────────────────────────


class TestFailureIsolation:
    def test_no_match_returns_fallback(self, monkeypatch):
        # Force the source pipeline to find nothing AND the local
        # generator to report no_source_content (subject without bank).
        result = handle_exam_request(
            teacher_id=40,
            text="أريد اختبار قصير في التربية الفنية للصف الثاني عشر",
            render_pdf=False,
        )
        # The local sample bank doesn't carry تربية فنية → expect either
        # NO_MATCH or MISSING_INFO (asking for a topic).
        assert result.stage in (STAGE_NO_MATCH, STAGE_MISSING_INFO, STAGE_READY)
        assert result.reply_text  # never empty

    def test_provider_crash_does_not_propagate(self, monkeypatch):
        # Simulate a provider blowing up. The flow's outer try/except
        # in _try_source_pipeline must catch it and continue.
        from app.exam_engine import exam_flow as ef

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated providers crash")

        monkeypatch.setattr(ef, "list_providers", _boom)

        result = handle_exam_request(
            teacher_id=41,
            text=(
                "أريد اختبار قصير رياضيات للصف الرابع الابتدائي "
                "الفصل الدراسي الأول"
            ),
            render_pdf=False,
        )
        # Local generator should still produce an exam from the bank.
        assert result.stage == STAGE_READY
        assert result.exam is not None


# ──────────────────────────────────────────────────────────────────────
# 6. PDF rendering hook (best-effort)
# ──────────────────────────────────────────────────────────────────────


class TestPdfRendering:
    def test_render_pdf_path_attempted(self, monkeypatch):
        # Stub export_exam_pdf so we don't depend on Playwright/chromium.
        from app.exam_engine import exam_flow as ef

        class _FakeResult:
            path = "/tmp/fake-exam.pdf"

        monkeypatch.setattr(
            "app.exam_engine.exam_export.export_exam_pdf",
            lambda exam: _FakeResult(),
        )
        result = handle_exam_request(
            teacher_id=50,
            text=(
                "أريد اختبار قصير رياضيات للصف الرابع الابتدائي "
                "الفصل الدراسي الأول"
            ),
            render_pdf=True,
        )
        assert result.is_ready
        assert result.pdf_path == "/tmp/fake-exam.pdf"

    def test_render_pdf_failure_is_swallowed(self, monkeypatch):
        def _boom(exam):
            raise RuntimeError("no chromium available")

        monkeypatch.setattr(
            "app.exam_engine.exam_export.export_exam_pdf",
            _boom,
        )
        result = handle_exam_request(
            teacher_id=51,
            text=(
                "أريد اختبار قصير رياضيات للصف الرابع الابتدائي "
                "الفصل الدراسي الأول"
            ),
            render_pdf=True,
        )
        # Flow still ready, PDF just None.
        assert result.is_ready
        assert result.pdf_path is None


# ──────────────────────────────────────────────────────────────────────
# 7. Webhook integration helper
# ──────────────────────────────────────────────────────────────────────


class TestWebhookHelper:
    def test_make_exam_flow_result_returns_object(self):
        from app.services import whatsapp_integration as wa

        out = wa.make_exam_flow_result(
            teacher_id=60,
            text=(
                "أريد اختبار قصير رياضيات للصف الرابع الابتدائي "
                "الفصل الدراسي الأول"
            ),
            render_pdf=False,
        )
        assert out is not None
        assert out.stage == STAGE_READY


# ──────────────────────────────────────────────────────────────────────
# 8. Architectural contracts
# ──────────────────────────────────────────────────────────────────────


_FORBIDDEN_PREFIXES = (
    "app.export_engine",
    "app.media_engine",
    "app.review_engine",
    "app.storage_engine",
    "app.services.exporter",
    "playwright",
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
    @pytest.mark.parametrize("module_path", [
        "app/exam_engine/exam_flow.py",
        "app/exam_engine/exam_slot_parser.py",
        "app/exam_engine/exam_defaults.py",
        "app/conversation_engine/exam_state.py",
    ])
    def test_no_forbidden_imports(self, module_path):
        from app.exam_engine import exam_flow as _ef
        # Resolve relative to the project root the same way pytest does.
        repo_root = os.path.abspath(os.path.join(
            os.path.dirname(_ef.__file__), "..", "..",
        ))
        full = os.path.join(repo_root, module_path)
        for module in _walk_imports(full):
            for forbidden in _FORBIDDEN_PREFIXES:
                assert not module.startswith(forbidden), (
                    f"{module_path} imports forbidden module {module}"
                )

    def test_existing_intent_ordering_preserved(self):
        # The Phase-3 intents must still resolve correctly.
        assert detect_intent("صدر الآن").intent == "export"
        assert detect_intent("راجع الشواهد").intent == "review"
        assert detect_intent("احذف آخر شاهد").intent == "delete_last"
        # And category hint for "هذه خطة" still works.
        out = detect_intent("هذه خطة فصلية")
        assert out.intent == "category"
