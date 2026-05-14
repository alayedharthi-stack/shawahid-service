"""
Phase 3 — unit tests for the exam_rewrite analysis pipeline.

Coverage matrix:
    1. text_cleaner          → strips boilerplate, keeps questions intact
    2. metadata_extractor    → subject / grade / exam_type / title /
                                instructions / total_score
    3. question_parser       → sections + numbered questions + choices +
                                type classification + scores
    4. analyze_exam_text     → end-to-end success on a clear exam
    5. analyze_exam_text     → worksheet → exam_type=worksheet
    6. analyze_exam_text     → empty / scanned PDF → warnings
    7. messages              → success reply contains the detected
                                subject/grade/question-count
    8. messages              → failure reply matches the canonical copy
    9. analyze_exam_pdf      → never generates a PDF, never touches DB,
                                never saves an evidence row.
   10. webhook integration  → _handle_exam_choice_rewrite uses the
                                analyser and replies appropriately.

All tests are pure: no DB, no network, no GPT.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.exam_rewrite import (
    EXAM_TYPE_ASSESSMENT,
    EXAM_TYPE_ASSIGNMENT,
    EXAM_TYPE_EXAM,
    EXAM_TYPE_WORKSHEET,
    QUESTION_TYPE_COMPLETE,
    QUESTION_TYPE_ESSAY,
    QUESTION_TYPE_MATCHING,
    QUESTION_TYPE_MULTIPLE_CHOICE,
    QUESTION_TYPE_SHORT_ANSWER,
    QUESTION_TYPE_TRUE_FALSE,
    QUESTION_TYPE_UNKNOWN,
    StructuredExam,
    analyze_exam_pdf,
    analyze_exam_text,
)
from app.exam_rewrite import metadata_extractor as meta
from app.exam_rewrite import question_parser as qparser
from app.exam_rewrite import text_cleaner as cleaner
from app.services import exam_rewrite_messages as msgs


# ──────────────────────────────────────────────────────────────────────
# 1. text_cleaner
# ──────────────────────────────────────────────────────────────────────


class TestTextCleaner:
    def test_drops_signature_and_name_lines(self):
        text = (
            "اختبار الفترة الأولى\n"
            "اسم المعلم: تركي الحارثي\n"
            "السؤال الأول: اختر الإجابة الصحيحة\n"
            "1) ما عاصمة المملكة؟\n"
            "أ. تركي العتيبي\n"
        )
        lines = cleaner.clean_lines(text)
        # The two boilerplate lines must be dropped …
        assert all("اسم المعلم" not in ln for ln in lines)
        assert all(ln != "أ. تركي العتيبي" for ln in lines)
        # … the question must remain.
        assert any("ما عاصمة المملكة" in ln for ln in lines)
        assert any("السؤال الأول" in ln for ln in lines)

    def test_strips_phone_numbers_and_emails(self):
        text = "للتواصل: 0501234567 أو teacher@school.edu.sa"
        out = cleaner.clean_text(text)
        assert "0501234567" not in out
        assert "teacher@school.edu.sa" not in out

    def test_keeps_question_marker_with_period(self):
        # ``1.`` is a legitimate question marker — must NOT be dropped.
        lines = cleaner.clean_lines("1. ما هو 5+5؟")
        assert any("ما هو 5+5" in ln for ln in lines)

    def test_drops_page_footer(self):
        out = cleaner.clean_text(
            "السؤال الأول\nصفحة 1 من 3\n1) ما عاصمة المملكة؟"
        )
        assert "صفحة 1 من 3" not in out
        assert "ما عاصمة المملكة" in out


# ──────────────────────────────────────────────────────────────────────
# 2. metadata_extractor
# ──────────────────────────────────────────────────────────────────────


class TestMetadataExtractor:
    def test_detect_subject_explicit_label(self):
        assert meta.detect_subject("المادة: الرياضيات") == "الرياضيات"
        assert meta.detect_subject("المادة: اللغة العربية") == "اللغة العربية"

    def test_detect_subject_inline_keyword(self):
        assert meta.detect_subject(
            "اختبار العلوم — الفصل الأول"
        ) == "العلوم"

    def test_detect_subject_returns_none(self):
        assert meta.detect_subject("") is None
        assert meta.detect_subject("نص عام بدون أي مادة") is None

    @pytest.mark.parametrize("text,expected", [
        ("الصف الخامس الابتدائي", "الصف الخامس ابتدائي"),
        ("الصف الثاني المتوسط",   "الصف الثاني متوسط"),
        ("الصف 5 الابتدائي",      "الصف الخامس ابتدائي"),
        ("الصف 11 الثانوي",       "الصف الحادي عشر ثانوي"),
    ])
    def test_detect_grade(self, text, expected):
        assert meta.detect_grade(text) == expected

    def test_detect_grade_returns_none(self):
        assert meta.detect_grade("") is None
        assert meta.detect_grade("اختبار عام") is None

    @pytest.mark.parametrize("text,expected", [
        ("اختبار الفترة الأولى",      EXAM_TYPE_EXAM),
        ("ورقة عمل — الكسور",         EXAM_TYPE_WORKSHEET),
        ("نموذج قياس مهارات",         EXAM_TYPE_ASSESSMENT),
        ("التقويم الأسبوعي",          EXAM_TYPE_ASSESSMENT),
        ("الواجب المنزلي",            EXAM_TYPE_ASSIGNMENT),
    ])
    def test_detect_exam_type(self, text, expected):
        assert meta.detect_exam_type(text) == expected

    def test_detect_exam_type_uses_hint(self):
        assert meta.detect_exam_type("", hint=EXAM_TYPE_WORKSHEET) == EXAM_TYPE_WORKSHEET

    def test_detect_title_picks_obvious_header(self):
        lines = [
            "[صفحة 1]",
            "وزارة التعليم",
            "اختبار الفترة الأولى — الرياضيات",
            "اسم الطالب: ___________",
            "السؤال الأول",
        ]
        title = meta.detect_title(lines)
        assert title is not None
        assert "اختبار" in title

    def test_detect_instructions(self):
        lines = [
            "اختبار",
            "تعليمات: اقرأ السؤال جيدًا قبل الإجابة",
            "السؤال الأول",
        ]
        assert "اقرأ السؤال جيدًا" in (meta.detect_instructions(lines) or "")

    @pytest.mark.parametrize("text,expected", [
        ("مجموع الدرجات: 20",         20.0),
        ("الدرجة الكلية: 100",        100.0),
        ("من 50 درجة",                50.0),
    ])
    def test_detect_total_score(self, text, expected):
        assert meta.detect_total_score(text) == expected

    def test_detect_total_score_none(self):
        assert meta.detect_total_score("") is None
        assert meta.detect_total_score("اختبار") is None


# ──────────────────────────────────────────────────────────────────────
# 3. question_parser
# ──────────────────────────────────────────────────────────────────────


class TestQuestionParser:
    def test_simple_numbered_questions_one_section(self):
        lines = [
            "1) ما عاصمة المملكة؟",
            "2) كم عدد أركان الإسلام؟",
            "3) من هو أول الخلفاء الراشدين؟",
        ]
        sections = qparser.parse_sections(lines)
        assert len(sections) == 1
        questions = sections[0].questions
        assert [q.number for q in questions] == [1, 2, 3]
        assert "عاصمة" in questions[0].text

    def test_explicit_sections_with_mcq(self):
        lines = [
            "السؤال الأول: اختر الإجابة الصحيحة (5 درجات)",
            "1) ما عاصمة المملكة العربية السعودية؟",
            "أ) جدة",
            "ب) الرياض",
            "ج) مكة",
            "د) المدينة",
            "2) كم عدد فصول السنة؟",
            "أ) ثلاثة",
            "ب) أربعة",
            "ج) خمسة",
            "د) ستة",
            "السؤال الثاني: ضع علامة صح أو خطأ (3 درجات)",
            "1) الصلاة عمود الدين",
            "2) عدد ركعات الفجر أربع",
        ]
        sections = qparser.parse_sections(lines)
        assert len(sections) == 2
        assert sections[0].score == 5.0
        assert sections[1].score == 3.0
        assert sections[0].questions[0].type == QUESTION_TYPE_MULTIPLE_CHOICE
        assert len(sections[0].questions[0].choices) == 4
        assert sections[1].questions[0].type == QUESTION_TYPE_TRUE_FALSE

    def test_inline_choices_on_one_line(self):
        lines = [
            "السؤال الأول: اختر الإجابة الصحيحة",
            "1) ما عاصمة المملكة؟ أ) جدة ب) الرياض ج) مكة د) المدينة",
        ]
        sections = qparser.parse_sections(lines)
        assert len(sections) == 1
        q = sections[0].questions[0]
        assert q.type == QUESTION_TYPE_MULTIPLE_CHOICE
        assert len(q.choices) == 4
        assert any("الرياض" in c for c in q.choices)

    def test_complete_blank_question_type(self):
        lines = [
            "السؤال الثالث: أكمل الفراغ",
            "1) ........... هي عاصمة المملكة",
            "2) عدد أركان الإسلام ........",
        ]
        sections = qparser.parse_sections(lines)
        assert sections[0].questions[0].type == QUESTION_TYPE_COMPLETE

    def test_matching_question_type(self):
        lines = [
            "السؤال الرابع: صل بين العمود الأول والثاني",
            "1) الصلاة - عماد الدين",
            "2) الزكاة - تطهير المال",
        ]
        sections = qparser.parse_sections(lines)
        assert sections[0].questions[0].type == QUESTION_TYPE_MATCHING

    def test_short_answer_and_essay(self):
        lines = [
            "السؤال الخامس: أجب عما يلي",
            "1) ما هو معنى التوكل؟",
            "2) اذكر فوائد الصلاة",
            "السؤال السادس: اكتب فقرة من خمسة أسطر",
            "1) اكتب فقرة عن أهمية العلم",
        ]
        sections = qparser.parse_sections(lines)
        assert sections[0].questions[0].type == QUESTION_TYPE_SHORT_ANSWER
        assert sections[1].questions[0].type == QUESTION_TYPE_ESSAY

    def test_per_question_score(self):
        lines = [
            "1) ما هو 5+5؟ (2)",
            "2) ما هو 6×3؟ (3)",
        ]
        sections = qparser.parse_sections(lines)
        assert sections[0].questions[0].score == 2.0
        assert sections[0].questions[1].score == 3.0

    def test_empty_input_returns_empty(self):
        assert qparser.parse_sections([]) == []


# ──────────────────────────────────────────────────────────────────────
# 4-6. analyze_exam_text (end-to-end)
# ──────────────────────────────────────────────────────────────────────


CLEAR_EXAM_TEXT = """\
وزارة التعليم
اختبار الفترة الأولى — مادة الرياضيات
الصف الخامس الابتدائي
مجموع الدرجات: 20
اسم المعلم: أ. تركي
تعليمات: اقرأ السؤال جيدًا

السؤال الأول: اختر الإجابة الصحيحة (10 درجات)
1) ما ناتج 5+5؟
أ) 8
ب) 9
ج) 10
د) 11
2) ما ناتج 6×3؟
أ) 18
ب) 20
ج) 15
د) 12

السؤال الثاني: ضع علامة صح أو خطأ (5 درجات)
1) العدد 7 عدد فردي
2) ناتج 4+4 يساوي 9

السؤال الثالث: أكمل الفراغ (5 درجات)
1) ....... هو ناتج 10 ÷ 2
2) ......... عدد فصول السنة
"""


WORKSHEET_TEXT = """\
ورقة عمل — درس الكسور
الصف الرابع الابتدائي
المادة: الرياضيات

1) أكمل الفراغ: ½ + ½ = ......
2) أكمل الفراغ: ¼ + ¼ = ......
3) أكمل الفراغ: ⅓ + ⅓ = ......
"""


class TestAnalyzeExamText:
    def test_clear_exam_full_structure(self):
        r = analyze_exam_text(CLEAR_EXAM_TEXT)
        assert r.subject == "الرياضيات"
        assert r.grade == "الصف الخامس ابتدائي"
        assert r.exam_type == EXAM_TYPE_EXAM
        assert r.title is not None and "اختبار" in r.title
        assert r.total_score == 20.0
        assert r.instructions is not None and "اقرأ السؤال" in r.instructions
        assert len(r.sections) == 3
        assert r.total_questions == 6
        # MCQ section came through with choices intact.
        assert r.sections[0].questions[0].type == QUESTION_TYPE_MULTIPLE_CHOICE
        assert len(r.sections[0].questions[0].choices) == 4
        # True/False section.
        assert r.sections[1].questions[0].type == QUESTION_TYPE_TRUE_FALSE
        # Complete-the-blank section.
        assert r.sections[2].questions[0].type == QUESTION_TYPE_COMPLETE
        assert r.is_usable()

    def test_worksheet_exam_type(self):
        r = analyze_exam_text(WORKSHEET_TEXT)
        assert r.exam_type == EXAM_TYPE_WORKSHEET
        assert r.subject == "الرياضيات"
        # Worksheet doesn't always have explicit sections — single
        # section with all 3 questions.
        assert r.total_questions == 3
        assert r.sections[0].questions[0].type == QUESTION_TYPE_COMPLETE
        assert r.is_usable()

    def test_empty_text_returns_warnings(self):
        r = analyze_exam_text("")
        assert isinstance(r, StructuredExam)
        assert r.total_questions == 0
        assert not r.is_usable()
        assert len(r.warnings) >= 1
        assert any("لم يتم استخراج نص" in w for w in r.warnings)

    def test_scanned_pdf_only_signatures_returns_warnings(self):
        text = "اسم المعلم: أ. تركي\nتوقيع المدير: ___________\n"
        r = analyze_exam_text(text)
        assert not r.is_usable()
        assert len(r.warnings) >= 1

    def test_teacher_subject_fills_blank(self):
        r = analyze_exam_text(
            "1) ما عاصمة المملكة؟\n2) كم فصلًا في السنة؟",
            teacher_subject="الدراسات الاجتماعية",
        )
        assert r.subject == "الدراسات الاجتماعية"
        assert r.total_questions == 2

    def test_to_dict_matches_phase3_spec_shape(self):
        r = analyze_exam_text(CLEAR_EXAM_TEXT)
        d = r.to_dict()
        assert set(d.keys()) == {
            "subject", "grade", "exam_type", "title",
            "instructions", "total_score", "sections", "warnings",
        }
        s0 = d["sections"][0]
        assert set(s0.keys()) == {"title", "score", "questions"}
        q0 = s0["questions"][0]
        assert set(q0.keys()) == {"number", "type", "text", "choices", "score"}


# ──────────────────────────────────────────────────────────────────────
# 7-8. exam_rewrite_messages
# ──────────────────────────────────────────────────────────────────────


class TestRewriteMessages:
    def test_success_message_contains_detected_fields(self):
        r = analyze_exam_text(CLEAR_EXAM_TEXT)
        msg = msgs.build_rewrite_analysis_success_message(r)
        assert "الرياضيات" in msg
        assert "الصف الخامس ابتدائي" in msg
        assert "عدد الأسئلة: 6" in msg
        assert "مجموع الدرجات: 20" in msg
        assert "المرحلة التالية" in msg

    def test_success_message_handles_missing_subject_and_grade(self):
        bare = analyze_exam_text(
            "1) ما عاصمة المملكة؟\n2) كم فصلًا في السنة؟"
        )
        msg = msgs.build_rewrite_analysis_success_message(bare)
        assert "غير محددة" in msg or "غير محدد" in msg
        assert "عدد الأسئلة: 2" in msg

    def test_failure_message_canonical(self):
        msg = msgs.build_rewrite_analysis_failure_message()
        assert "لم أستطع تحليل" in msg
        assert "أعد إرسال الملف" in msg

    def test_success_message_uses_type_label(self):
        r = analyze_exam_text(WORKSHEET_TEXT)
        msg = msgs.build_rewrite_analysis_success_message(r)
        assert "ورقة العمل" in msg or "ورقة عمل" in msg


# ──────────────────────────────────────────────────────────────────────
# 9. Side-effect guard: analyze_exam_pdf never generates PDF / DB / save
# ──────────────────────────────────────────────────────────────────────


class TestNoSideEffects:
    def test_analyze_exam_pdf_with_missing_file_returns_warnings(self):
        # Pure function: missing path → warnings, never raises.
        r = analyze_exam_pdf("/no/such/file.pdf")
        assert isinstance(r, StructuredExam)
        assert not r.is_usable()
        assert len(r.warnings) >= 1

    def test_analyze_exam_pdf_with_fallback_text(self):
        r = analyze_exam_pdf(
            None,
            fallback_text=CLEAR_EXAM_TEXT,
            detected_type_hint=EXAM_TYPE_EXAM,
        )
        assert r.is_usable()
        assert r.subject == "الرياضيات"

    def test_exam_rewrite_package_has_no_html_or_pdf_codegen(self):
        # Guard against accidentally adding generation in Phase 3.
        # We check the public surface — no name should refer to PDF
        # or HTML generation.
        import app.exam_rewrite as pkg
        for name in pkg.__all__:
            assert "html" not in name.lower()
            assert "render" not in name.lower()
            assert "generate_pdf" not in name.lower()


# ──────────────────────────────────────────────────────────────────────
# 10. Webhook integration: _handle_exam_choice_rewrite
# ──────────────────────────────────────────────────────────────────────


def _fake_teacher(id_=11):
    return SimpleNamespace(
        id=id_,
        phone="+966500000000",
        name="أ. تركي",
        subject="الرياضيات",
        stage="ابتدائي",
        grades="الخامس",
        school_name="مدرسة الأمل",
        principal_name=None,
        region=None,
        education_admin=None,
        welcome_sent_at=None,
    )


class _BgTasks:
    def __init__(self):
        self.calls: list[tuple] = []

    def add_task(self, func, *args, **kwargs):
        self.calls.append((func, args, kwargs))


class TestWebhookRewriteHandler:
    def test_usable_analysis_sends_success_reply(self, monkeypatch):
        from app.api import webhook as wh

        teacher = _fake_teacher()
        bg = _BgTasks()

        # Stub: pretend create_evidence + save would explode if called.
        def _explode(**kw):
            raise AssertionError(
                "create_evidence must NOT run in the rewrite path"
            )
        monkeypatch.setattr(wh, "create_evidence", _explode)
        monkeypatch.setattr(wh, "generate_pdf_preview", lambda p: None)

        wh._handle_exam_choice_rewrite(
            teacher=teacher,
            background_tasks=bg,
            storage_path=None,
            extracted_text=CLEAR_EXAM_TEXT,
            detected_type="exam",
            log_context="exam_choice_rewrite",
        )

        contexts = [c[2].get("context") for c in bg.calls]
        assert "exam_choice_rewrite_success" in contexts
        # The reply body must summarise the detected metadata.
        send_args = [c for c in bg.calls if c[2].get("context") == "exam_choice_rewrite_success"]
        reply_body = send_args[0][1][1]
        assert "الرياضيات" in reply_body
        assert "عدد الأسئلة:" in reply_body

    def test_unusable_analysis_sends_failure_reply(self, monkeypatch):
        from app.api import webhook as wh

        teacher = _fake_teacher()
        bg = _BgTasks()

        def _explode(**kw):
            raise AssertionError("create_evidence must NOT run in the rewrite path")
        monkeypatch.setattr(wh, "create_evidence", _explode)

        # Empty text → analyser returns a non-usable StructuredExam.
        wh._handle_exam_choice_rewrite(
            teacher=teacher,
            background_tasks=bg,
            storage_path=None,
            extracted_text="",
            detected_type="exam",
            log_context="exam_choice_rewrite",
        )
        contexts = [c[2].get("context") for c in bg.calls]
        assert "exam_choice_rewrite_failure" in contexts

    def test_rewrite_handler_does_not_touch_normal_save_path(self, monkeypatch):
        """Regression: the rewrite handler must not import/call any
        evidence-saving function. We monkey-patch every dangerous call
        site to raise — if the handler still completes successfully
        nothing was saved."""
        from app.api import webhook as wh

        teacher = _fake_teacher()
        bg = _BgTasks()

        def _explode(*a, **k):
            raise AssertionError(
                "evidence save path was reached from rewrite flow"
            )
        monkeypatch.setattr(wh, "create_evidence", _explode)
        monkeypatch.setattr(wh, "set_enrichment_teacher_context", _explode)
        monkeypatch.setattr(wh, "is_exact_duplicate", _explode)
        monkeypatch.setattr(wh, "get_evidence_by_hash", _explode)

        wh._handle_exam_choice_rewrite(
            teacher=teacher,
            background_tasks=bg,
            storage_path=None,
            extracted_text=CLEAR_EXAM_TEXT,
            detected_type="exam",
            log_context="exam_choice_rewrite",
        )
        # If we got here the save path was never reached.
        assert bg.calls
