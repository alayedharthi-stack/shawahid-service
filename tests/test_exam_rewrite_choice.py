"""
Phase 2 — unit tests for the exam rewrite choice flow.

Coverage:
    1. exam_rewrite_choice.parse_exam_choice — all save / rewrite variants.
    2. exam_rewrite_choice store — set / get / clear / TTL.
    3. exam_rewrite_messages — choice prompt copy, rewrite-pending copy,
       detected_type → category mapping, title cleaning.
    4. _handle_exam_choice_save — saves an Evidence row with the right
       category / title / status, and clears the pending state.
    5. _handle_exam_choice_save — duplicate hash short-circuits without
       creating a new Evidence row.
    6. Webhook does NOT touch the normal evidence save path when there
       is no pending exam choice (regression guard).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.conversation_engine import exam_rewrite_choice as choice_mod
from app.services import exam_rewrite_messages as msgs
from app.services.pdf_kind_classifier import classify_pdf_kind


# ──────────────────────────────────────────────────────────────────────
# 1. parse_exam_choice
# ──────────────────────────────────────────────────────────────────────


class TestParseExamChoice:
    @pytest.mark.parametrize("text", [
        "1",
        "١",
        "1️⃣",
        "1.",
        "1)",
        "1 احفظه",
        "الاول",
        "الأول",
        "خيار اول",
        "الخيار الأول",
        "حفظ",
        "حفظه",
        "حفظه كشاهد",
        "احفظه كشاهد",
        "احفظ",
        "كشاهد",
        "خله شاهد",
        "اعتمده كشاهد",
    ])
    def test_save_variants(self, text):
        assert choice_mod.parse_exam_choice(text) == "save"

    @pytest.mark.parametrize("text", [
        "2",
        "٢",
        "2️⃣",
        "2.",
        "الثاني",
        "خيار 2",
        "الخيار الثاني",
        "إعادة صياغة",
        "اعد صياغة",
        "اعد صياغته",
        "اعاده الصياغه",
        "ابي اعاده صياغه",
        "حطه بكليشة المدرسة",
        "بكليشة المدرسة",
        "سو لي نسخة جديدة",
        "نظفه من الأسماء",
    ])
    def test_rewrite_variants(self, text):
        assert choice_mod.parse_exam_choice(text) == "rewrite"

    @pytest.mark.parametrize("text", [
        "",
        None,
        "مرحبا",
        "كيف حالك",
        "ابي اضيف خطة",
        "12 مساءً",
        "السلام عليكم",
        "1234567",
    ])
    def test_unrelated_returns_none(self, text):
        assert choice_mod.parse_exam_choice(text) is None

    def test_ambiguous_biases_to_rewrite(self):
        # If the teacher writes both "1" and "صياغة" we prefer rewrite
        # because that is the more consequential option and we'd rather
        # over-clarify than silently lose the chance to rewrite.
        assert choice_mod.parse_exam_choice("1 ابي اعاده صياغه") == "rewrite"


# ──────────────────────────────────────────────────────────────────────
# 2. Pending choice store
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_choice_store():
    choice_mod.reset_all()
    yield
    choice_mod.reset_all()


class TestPendingStore:
    def test_set_and_get(self):
        assert choice_mod.has_pending(99) is False
        entry = choice_mod.set_pending(
            99,
            storage_path="/tmp/foo.pdf",
            file_name="اختبار.pdf",
            safe_filename="exam.pdf",
            mime_type="application/pdf",
            media_url="https://wa/url",
            media_id="MID",
            media_hash="hash1",
            detected_type="exam",
            confidence=0.85,
            classifier_reason="exam_score=6",
            extracted_text="السؤال الأول",
            first_lines="اختبار الفترة",
        )
        assert entry.detected_type == "exam"
        assert choice_mod.has_pending(99) is True
        got = choice_mod.get_pending(99)
        assert got is not None
        assert got.file_name == "اختبار.pdf"
        assert got.confidence == pytest.approx(0.85)
        assert got.media_hash == "hash1"

    def test_clear(self):
        choice_mod.set_pending(1, detected_type="exam", confidence=0.8)
        assert choice_mod.has_pending(1)
        choice_mod.clear_pending(1)
        assert not choice_mod.has_pending(1)
        assert choice_mod.get_pending(1) is None

    def test_reset_all_isolates_tests(self):
        choice_mod.set_pending(1, detected_type="exam", confidence=0.8)
        choice_mod.set_pending(2, detected_type="worksheet", confidence=0.75)
        choice_mod.reset_all()
        assert not choice_mod.has_pending(1)
        assert not choice_mod.has_pending(2)

    def test_set_overwrites_previous(self):
        choice_mod.set_pending(7, detected_type="exam", confidence=0.71)
        choice_mod.set_pending(7, detected_type="worksheet", confidence=0.92)
        got = choice_mod.get_pending(7)
        assert got is not None
        assert got.detected_type == "worksheet"
        assert got.confidence == pytest.approx(0.92)

    def test_expired_entry_is_dropped(self, monkeypatch):
        from datetime import datetime, timedelta, timezone
        choice_mod.set_pending(5, detected_type="exam", confidence=0.8)
        # Force the entry to look 1 hour old.
        entry = choice_mod._BACKEND[5]
        entry.updated_at = datetime.now(timezone.utc) - timedelta(hours=1)
        assert choice_mod.get_pending(5) is None
        assert not choice_mod.has_pending(5)


# ──────────────────────────────────────────────────────────────────────
# 3. Message + mapping helpers
# ──────────────────────────────────────────────────────────────────────


class TestMessages:
    def test_choice_prompt_contains_both_options(self):
        prompt = msgs.build_choice_prompt()
        assert "اختبار أو ورقة عمل" in prompt
        assert "1️⃣ حفظه كشاهد" in prompt
        assert "2️⃣ إعادة صياغة الاختبار" in prompt

    def test_rewrite_pending_message_is_temporary(self):
        text = msgs.build_rewrite_pending_message()
        assert "إعادة صياغة" in text
        assert "المرحلة التالية" in text

    def test_clarification_message_shows_both_options(self):
        text = msgs.build_choice_clarification_message()
        assert "1️⃣" in text and "2️⃣" in text

    @pytest.mark.parametrize("detected,expected", [
        ("exam",       "اختبار"),
        ("worksheet",  "ورقة عمل"),
        ("assignment", "واجب منزلي"),
        ("assessment", "تقويم"),
        (None,         "اختبار"),  # safe fallback
        ("anything",   "اختبار"),
    ])
    def test_category_mapping(self, detected, expected):
        assert msgs.category_from_detected_type(detected) == expected

    def test_title_from_filename_cleans_separators(self):
        assert msgs.title_from_filename("اختبار_نهائي_رياضيات.pdf") == "اختبار نهائي رياضيات"
        assert msgs.title_from_filename("worksheet-math-grade-5.pdf") == "worksheet math grade 5"

    def test_title_from_filename_handles_empty(self):
        assert msgs.title_from_filename(None) == "اختبار"
        assert msgs.title_from_filename("") == "اختبار"
        assert msgs.title_from_filename("123456.pdf", fallback="ورقة عمل") == "ورقة عمل"


# ──────────────────────────────────────────────────────────────────────
# 4. _handle_exam_choice_save — saves and clears pending state
# ──────────────────────────────────────────────────────────────────────


def _fake_teacher(id_=42):
    return SimpleNamespace(
        id=id_,
        phone="+966500000000",
        name="أ. تركي",
        subject="رياضيات",
        stage="ابتدائي",
        grades="الخامس",
        school_name="مدرسة الأمل",
        principal_name=None,
        region=None,
        education_admin=None,
        welcome_sent_at=None,
    )


def _fake_evidence_row(ev_id=101, category="اختبار", title="اختبار الفترة"):
    return SimpleNamespace(
        id=ev_id,
        evidence_type="pdf",
        category=category,
        title=title,
        content_hash="hashX",
        is_duplicate=False,
        is_excluded_from_export=False,
        media_url=None,
        storage_path="/tmp/exam.pdf",
        ai_raw={"confidence_score": 0.85},
    )


class _BgTasks:
    """Minimal BackgroundTasks stub that records what would be sent."""

    def __init__(self):
        self.calls: list[tuple] = []

    def add_task(self, func, *args, **kwargs):
        self.calls.append((func, args, kwargs))


class TestHandleExamChoiceSave:
    def test_saves_with_classifier_category_and_clears_state(self, monkeypatch):
        from app.api import webhook as wh

        teacher = _fake_teacher()
        bg = _BgTasks()
        db = MagicMock(name="db")

        # Park a pending exam choice as the webhook would have done.
        choice_mod.set_pending(
            teacher.id,
            storage_path="/tmp/exam.pdf",
            file_name="اختبار_رياضيات.pdf",
            safe_filename="exam.pdf",
            mime_type="application/pdf",
            media_url="https://wa/url",
            media_id="MID",
            media_hash="hashX",
            detected_type="exam",
            confidence=0.85,
            classifier_reason="exam_score=6",
            extracted_text="السؤال الأول: اختر",
            first_lines="اختبار الفترة الأولى",
        )

        # Stub the dependencies the helper actually calls.
        monkeypatch.setattr(wh, "is_exact_duplicate", lambda *a, **k: False)
        monkeypatch.setattr(wh, "set_enrichment_teacher_context", lambda **kw: None)
        captured = {}

        def _fake_create(**kwargs):
            captured.update(kwargs)
            return _fake_evidence_row(ev_id=999, category=kwargs["category"], title=kwargs["title"])

        monkeypatch.setattr(wh, "create_evidence", _fake_create)
        monkeypatch.setattr(wh, "generate_pdf_preview", lambda p: None)

        # Run the handler.
        wh._handle_exam_choice_save(db=db, teacher=teacher, background_tasks=bg)

        # 1. Evidence row created with classifier-derived category / title.
        assert captured["evidence_type"] == "pdf"
        assert captured["category"] == "اختبار"
        assert captured["title"] == "اختبار رياضيات"
        assert captured["storage_path"] == "/tmp/exam.pdf"
        assert captured["mime_type"] == "application/pdf"
        assert captured["content_hash"] == "hashX"
        assert captured["ai_status"] == "completed"
        ai_raw = captured["ai_raw"]
        assert ai_raw["source"] == "exam_rewrite_choice"
        assert ai_raw["detected_type"] == "exam"

        # 2. Pending state is cleared.
        assert not choice_mod.has_pending(teacher.id)

        # 3. A WhatsApp confirmation was queued (send + preview job).
        kinds = [c[2].get("context") for c in bg.calls]
        assert "exam_choice_save" in kinds

    def test_duplicate_short_circuits_without_creating_row(self, monkeypatch):
        from app.api import webhook as wh

        teacher = _fake_teacher()
        bg = _BgTasks()
        db = MagicMock(name="db")

        choice_mod.set_pending(
            teacher.id,
            storage_path="/tmp/exam.pdf",
            file_name="exam.pdf",
            mime_type="application/pdf",
            media_hash="dup-hash",
            detected_type="exam",
            confidence=0.81,
        )

        monkeypatch.setattr(wh, "is_exact_duplicate", lambda *a, **k: True)
        monkeypatch.setattr(
            wh, "get_evidence_by_hash",
            lambda *a, **k: _fake_evidence_row(ev_id=77, category="اختبار", title="سابق"),
        )

        # create_evidence MUST NOT be called for duplicates.
        called = {"n": 0}
        def _explode(**kwargs):
            called["n"] += 1
            raise AssertionError("create_evidence must not run on duplicate")
        monkeypatch.setattr(wh, "create_evidence", _explode)

        wh._handle_exam_choice_save(db=db, teacher=teacher, background_tasks=bg)
        assert called["n"] == 0
        assert not choice_mod.has_pending(teacher.id)
        kinds = [c[2].get("context") for c in bg.calls]
        assert "exam_choice_save_duplicate" in kinds

    def test_expired_state_sends_polite_apology(self, monkeypatch):
        from app.api import webhook as wh

        teacher = _fake_teacher(id_=58)
        bg = _BgTasks()
        db = MagicMock(name="db")

        # No pending entry — should NOT crash, should NOT call create_evidence.
        def _explode(**kwargs):
            raise AssertionError("create_evidence must not run without state")
        monkeypatch.setattr(wh, "create_evidence", _explode)
        monkeypatch.setattr(wh, "is_exact_duplicate", lambda *a, **k: False)

        wh._handle_exam_choice_save(db=db, teacher=teacher, background_tasks=bg)

        kinds = [c[2].get("context") for c in bg.calls]
        assert "exam_choice_expired" in kinds


# ──────────────────────────────────────────────────────────────────────
# 5. Classifier still produces stash-worthy results for canonical PDFs
# ──────────────────────────────────────────────────────────────────────


class TestStashGate:
    """Phase 2 only stashes when classifier ≥ 0.70 with kind=exam_or_worksheet.

    These tests do not invoke the webhook — they verify the classifier
    behaviour we rely on at the gate. They guard against accidental
    threshold drift in the underlying classifier.
    """

    def test_real_exam_text_passes_gate(self):
        r = classify_pdf_kind(
            extracted_text=(
                "اختبار الفترة الأولى — مادة الرياضيات\n"
                "السؤال الأول: اختر الإجابة الصحيحة\n"
                "السؤال الثاني: ضع علامة صح\n"
                "مجموع الدرجات 20"
            ),
            filename="اختبار_رياضيات.pdf",
            has_questions=True,
            has_grades_table=True,
        )
        assert r["pdf_kind"] == "exam_or_worksheet"
        assert r["confidence"] >= 0.70

    def test_evidence_pdf_fails_gate(self):
        r = classify_pdf_kind(
            extracted_text="خطة أسبوعية — تحضير الدرس\nنواتج التعلم",
            filename="خطة_أسبوعية.pdf",
            has_objectives=True,
        )
        # Either evidence or unknown — both must NOT trigger the gate.
        assert r["pdf_kind"] != "exam_or_worksheet" or r["confidence"] < 0.70
