"""
tests/test_exam_engine.py — phase-10 exam_engine foundation.

Required scenarios from the phase brief:

    • ExamProfile fills teacher data.
    • template.html renders without the shawahid template.
    • Exam type, subject, grade, school appear in the rendered header.
    • total_marks equals the sum of question marks.
    • A question without a correct answer fails validation.
    • A duplicate question fails validation.
    • An unclear source_mode triggers "missing info" reply.
    • exam_engine does not import export_engine.
    • exam_engine does not touch templates/exports/ministry_v1.
    • Exam PDF render smoke test (without real Playwright).
"""
from __future__ import annotations

import ast
import os

import pytest

from app.curriculum_engine.schemas import LearningOutcome
from app.exam_engine import (
    EXAM_TYPE_QUICK,
    QTYPE_FILL_BLANK,
    QTYPE_MCQ,
    QTYPE_SHORT,
    QTYPE_TRUE_FALSE,
    SOURCE_CURRICULUM,
    SOURCE_MANUAL_TOPIC,
    SOURCE_SAMPLE_BANK,
    ExamProfile,
    ExamQuestion,
    ExamRequest,
    GenerationFailure,
    KutubiProvider,
    LocalSamplesProvider,
    MadatiProvider,
    ManhajiProvider,
    build_exam_failure_message,
    build_exam_missing_info_message,
    build_exam_prompt,
    build_exam_profile,
    build_exam_ready_message,
    build_exam_request_message,
    export_exam_pdf,
    generate_exam,
    list_providers,
    render_exam_html,
    validate_exam,
    sources,
)


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def base_request():
    return ExamRequest(
        teacher_id=1,
        exam_type=EXAM_TYPE_QUICK,
        subject="الرياضيات",
        grade="الصف الرابع",
        stage="المرحلة الابتدائية",
        semester="الفصل الدراسي الأول",
        topic="جمع وطرح الأعداد",
        question_types=(QTYPE_MCQ, QTYPE_TRUE_FALSE, QTYPE_SHORT),
        total_questions=3,
        total_marks=10,
        duration_minutes=30,
        source_mode=SOURCE_MANUAL_TOPIC,
    )


@pytest.fixture
def base_profile(base_request):
    return build_exam_profile(
        request=base_request,
        teacher_name="إياد محمد الحارثي",
        school_name="ابتدائية الفيصل",
        education_admin="إدارة تعليم الرياض",
        region="الرياض",
        academic_year="1446هـ",
    )


# ──────────────────────────────────────────────────────────────────────
# Profile + DTO basics
# ──────────────────────────────────────────────────────────────────────


class TestExamProfile:
    def test_profile_carries_all_fields(self, base_profile):
        assert base_profile.teacher_name == "إياد محمد الحارثي"
        assert base_profile.school_name == "ابتدائية الفيصل"
        assert base_profile.education_admin == "إدارة تعليم الرياض"
        assert base_profile.region == "الرياض"
        assert base_profile.subject == "الرياضيات"
        assert base_profile.grade == "الصف الرابع"
        assert base_profile.exam_type == EXAM_TYPE_QUICK
        assert base_profile.country == "المملكة العربية السعودية"
        assert base_profile.ministry == "وزارة التعليم"

    def test_exam_type_label_is_arabic(self, base_profile):
        assert "اختبار" in base_profile.exam_type_label()

    def test_required_fields_missing_detects_topic_gap(self):
        req = ExamRequest(
            teacher_id=1, subject="العلوم", grade="الصف الرابع",
            source_mode=SOURCE_MANUAL_TOPIC,
        )
        missing = req.required_fields_missing()
        assert "topic" in missing


# ──────────────────────────────────────────────────────────────────────
# Generator
# ──────────────────────────────────────────────────────────────────────


class TestExamGenerator:
    def test_generates_from_manual_topic(self, base_request, base_profile):
        exam = generate_exam(base_request, profile=base_profile)
        assert not isinstance(exam, GenerationFailure)
        assert exam.question_count >= 1
        assert exam.profile.subject == "الرياضيات"

    def test_total_marks_match_request(self, base_request, base_profile):
        exam = generate_exam(base_request, profile=base_profile)
        assert not isinstance(exam, GenerationFailure)
        assert abs(exam.actual_total_marks - base_request.total_marks) < 0.05

    def test_unclear_source_returns_failure(self):
        req = ExamRequest(
            teacher_id=1,
            subject="الرياضيات",
            grade="الصف الرابع",
            source_mode=SOURCE_MANUAL_TOPIC,
            # No topic / lesson / unit.
        )
        out = generate_exam(req)
        assert isinstance(out, GenerationFailure)
        assert "topic" in out.missing or "topic_or_sample" in out.missing

    def test_missing_required_fields_failure(self):
        req = ExamRequest(teacher_id=1, source_mode=SOURCE_MANUAL_TOPIC)
        out = generate_exam(req)
        assert isinstance(out, GenerationFailure)
        assert out.missing  # at least one missing field

    def test_curriculum_mode_uses_outcomes(self, base_profile):
        outcomes = (
            LearningOutcome(raw="يحل الطالب المعادلات الخطية", verb="يحل",
                            bloom_level="application"),
            LearningOutcome(raw="يفسر الطالب العلاقة بين المتغيرين",
                            verb="يفسر", bloom_level="comprehension"),
        )
        req = ExamRequest(
            teacher_id=1, subject="الرياضيات", grade="الصف الرابع",
            stage="المرحلة الابتدائية",
            source_mode=SOURCE_CURRICULUM,
            total_questions=2, total_marks=4,
            question_types=(QTYPE_SHORT,),
        )
        exam = generate_exam(req, profile=base_profile,
                             learning_outcomes=outcomes)
        assert not isinstance(exam, GenerationFailure)
        assert exam.question_count == 2
        assert all(q.learning_outcome for q in exam.questions)

    def test_sample_bank_returns_questions(self):
        req = ExamRequest(
            teacher_id=2, subject="الرياضيات",
            stage="المرحلة الابتدائية", grade="الصف الرابع",
            source_mode=SOURCE_SAMPLE_BANK,
            total_questions=3, total_marks=6,
        )
        exam = generate_exam(req)
        assert not isinstance(exam, GenerationFailure)
        assert exam.question_count >= 1


# ──────────────────────────────────────────────────────────────────────
# Validator
# ──────────────────────────────────────────────────────────────────────


class TestExamValidator:
    def test_valid_exam_passes(self, base_request, base_profile):
        exam = generate_exam(base_request, profile=base_profile)
        result = validate_exam(exam)
        assert result.is_valid, [i.message for i in result.errors]

    def test_question_without_answer_fails(self, base_profile):
        from app.exam_engine.schemas import GeneratedExam, ExamRequest as _ER
        bad = ExamQuestion(
            id="x1", type=QTYPE_FILL_BLANK,
            text="ما عاصمة المملكة؟", correct_answer="", marks=2,
        )
        # Pad with a valid question so total marks reach 4 (avoid mismatch).
        good = ExamQuestion(
            id="x2", type=QTYPE_TRUE_FALSE,
            text="الرياض في وسط المملكة.",
            correct_answer="صح", marks=2,
        )
        exam = GeneratedExam(
            profile=ExamProfile(
                teacher_name="ت", subject="الاجتماعيات",
                exam_type=EXAM_TYPE_QUICK, total_marks=4,
            ),
            questions=(bad, good),
            request=_ER(teacher_id=1, subject="الاجتماعيات",
                        grade="الصف الرابع",
                        source_mode=SOURCE_MANUAL_TOPIC, topic="عواصم"),
        )
        result = validate_exam(exam)
        assert not result.is_valid
        codes = {i.code for i in result.errors}
        assert "missing_answer" in codes

    def test_duplicate_question_fails(self):
        from app.exam_engine.schemas import GeneratedExam, ExamRequest as _ER
        q1 = ExamQuestion(id="d1", type=QTYPE_TRUE_FALSE,
                          text="2 + 2 = 4", correct_answer="صح", marks=1)
        q2 = ExamQuestion(id="d2", type=QTYPE_TRUE_FALSE,
                          text="2 + 2 = 4", correct_answer="صح", marks=1)
        exam = GeneratedExam(
            profile=ExamProfile(
                exam_type=EXAM_TYPE_QUICK, total_marks=2,
                subject="الرياضيات",
            ),
            questions=(q1, q2),
            request=_ER(teacher_id=1, subject="الرياضيات",
                        grade="الصف الرابع",
                        source_mode=SOURCE_MANUAL_TOPIC, topic="جمع"),
        )
        result = validate_exam(exam)
        codes = {i.code for i in result.errors}
        assert "duplicate_question" in codes

    def test_marks_mismatch_fails(self):
        from app.exam_engine.schemas import GeneratedExam, ExamRequest as _ER
        q = ExamQuestion(id="m1", type=QTYPE_TRUE_FALSE,
                         text="2 + 2 = 4", correct_answer="صح", marks=1)
        exam = GeneratedExam(
            profile=ExamProfile(
                exam_type=EXAM_TYPE_QUICK, total_marks=10,
                subject="الرياضيات",
            ),
            questions=(q,),
            request=_ER(teacher_id=1, subject="الرياضيات",
                        grade="الصف الرابع",
                        source_mode=SOURCE_MANUAL_TOPIC, topic="جمع"),
        )
        result = validate_exam(exam)
        codes = {i.code for i in result.errors}
        assert "marks_mismatch" in codes


# ──────────────────────────────────────────────────────────────────────
# Renderer / template isolation
# ──────────────────────────────────────────────────────────────────────


class TestExamRenderer:
    def test_renders_html_with_header_meta(self, base_request, base_profile):
        exam = generate_exam(base_request, profile=base_profile)
        html = render_exam_html(exam)
        assert "<!DOCTYPE html>" in html
        assert 'dir="rtl"' in html
        # Ministry / country / school appear
        assert "وزارة التعليم" in html
        assert "ابتدائية الفيصل" in html
        assert "إدارة تعليم الرياض" in html
        # Subject + grade + exam type label
        assert "الرياضيات" in html
        assert "الصف الرابع" in html
        assert "اختبار قصير" in html
        # Generator-built questions appear
        assert exam.questions[0].text in html

    def test_template_independent_from_shawahid(self, base_request, base_profile):
        exam = generate_exam(base_request, profile=base_profile)
        html = render_exam_html(exam)
        # The shawahid template's signature classes must not appear.
        assert "section-hero" not in html
        assert "evidence-card" not in html
        assert "ministry_v1" not in html

    def test_missing_optional_fields_show_placeholders(self, base_request):
        exam = generate_exam(base_request)
        assert exam is not None
        html = render_exam_html(exam)
        # When teacher / school weren't supplied, the dotted placeholder
        # appears in the header.
        assert "............" in html


# ──────────────────────────────────────────────────────────────────────
# PDF export smoke (no real Playwright)
# ──────────────────────────────────────────────────────────────────────


class TestExamPdfSmoke:
    def test_pdf_export_returns_html_only_when_playwright_disabled(
        self, base_request, base_profile,
    ):
        exam = generate_exam(base_request, profile=base_profile)
        result = export_exam_pdf(exam, use_playwright=False)
        assert result.backend == "html_only"
        assert result.html
        assert result.pdf_bytes is None

    def test_pdf_export_falls_back_when_playwright_unavailable(
        self, monkeypatch, base_request, base_profile,
    ):
        # Force the Playwright import to fail.
        import builtins
        real_import = builtins.__import__

        def _import(name, *args, **kwargs):
            if name.startswith("playwright"):
                raise ImportError("forced for test")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _import)

        exam = generate_exam(base_request, profile=base_profile)
        result = export_exam_pdf(exam, use_playwright=True)
        assert result.backend == "html_only"
        assert "playwright_unavailable" in result.notes


# ──────────────────────────────────────────────────────────────────────
# WhatsApp message builders
# ──────────────────────────────────────────────────────────────────────


class TestExamMessages:
    def test_request_message_lists_required_fields(self):
        msg = build_exam_request_message()
        assert "📘" in msg and "🏫" in msg
        assert "المادة" in msg and "نوع الاختبار" in msg

    def test_missing_info_message_only_lists_missing(self):
        msg = build_exam_missing_info_message(("subject",))
        assert "المادة" in msg
        assert "الصف" not in msg

    def test_ready_message_contains_summary(self, base_request, base_profile):
        # Phase-13: the ready text was warmed up — the celebration line
        # is now "تم تجهيز اختبارك بنجاح 🎉".
        exam = generate_exam(base_request, profile=base_profile)
        msg = build_exam_ready_message(exam)
        assert "تم تجهيز اختبارك بنجاح" in msg
        assert "الرياضيات" in msg

    def test_failure_message_with_missing(self):
        msg = build_exam_failure_message("test", missing=("subject",))
        assert "المادة" in msg


# ──────────────────────────────────────────────────────────────────────
# Sources
# ──────────────────────────────────────────────────────────────────────


class TestSources:
    def test_external_providers_default_to_disabled(self):
        # Phase-11: providers no longer raise. They use the pluggable
        # HttpClient and the default ``DisabledHttpClient`` returns
        # nothing — so fetch yields an empty iterable.
        from app.exam_engine.sources.base import SourceQuery
        for prov_cls in (MadatiProvider, KutubiProvider, ManhajiProvider):
            result = list(prov_cls().fetch(SourceQuery(subject="الرياضيات")))
            assert result == []

    def test_local_provider_returns_questions(self):
        from app.exam_engine.sources.base import SourceQuery
        prov = LocalSamplesProvider()
        samples = list(prov.fetch(SourceQuery(
            subject="الرياضيات", stage="المرحلة الابتدائية",
        )))
        assert samples
        questions = prov.extract_questions(samples[0])
        assert questions
        report = prov.quality_check(samples[0], questions)
        assert report.is_acceptable

    def test_list_active_providers_excludes_stubs(self):
        active = list_providers(only_active=True)
        names = {p.name for p in active}
        assert "local_samples" in names
        assert "madati" not in names
        assert "kutubi" not in names
        assert "manhaji" not in names

    def test_list_all_providers_includes_stubs(self):
        all_p = list_providers(only_active=False)
        names = {p.name for p in all_p}
        for required in ("local_samples", "madati", "kutubi", "manhaji"):
            assert required in names


# ──────────────────────────────────────────────────────────────────────
# Prompt builder
# ──────────────────────────────────────────────────────────────────────


class TestPromptBuilder:
    def test_prompt_includes_required_metadata(self, base_request):
        prompt = build_exam_prompt(base_request)
        assert "الرياضيات" in prompt
        assert "الصف الرابع" in prompt
        assert "اختبار قصير" in prompt
        assert "اختيار من متعدد" in prompt
        assert "10" in prompt  # total marks

    def test_prompt_includes_constraints(self, base_request):
        prompt = build_exam_prompt(base_request)
        assert "لا تنسخ" in prompt
        assert "مكررة" in prompt


# ──────────────────────────────────────────────────────────────────────
# Architectural contracts
# ──────────────────────────────────────────────────────────────────────

_FORBIDDEN_PREFIXES = (
    "app.export_engine",
    "app.media_engine",
    "app.review_engine",
    "app.storage_engine",
    "app.services.exporter",
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


def _python_files(root: str):
    for dirpath, _, filenames in os.walk(root):
        for fname in filenames:
            if fname.endswith(".py"):
                yield os.path.join(dirpath, fname)


class TestArchitecturalContracts:
    def test_no_forbidden_imports(self):
        from app import exam_engine
        pkg_root = os.path.dirname(exam_engine.__file__)
        for full in _python_files(pkg_root):
            for module in _walk_imports(full):
                for forbidden in _FORBIDDEN_PREFIXES:
                    assert not module.startswith(forbidden), (
                        f"{full} imports forbidden module {module}"
                    )

    def test_template_directory_is_independent(self):
        # The exam template MUST live under templates/exams, not exports.
        from app.exam_engine.exam_template import _exam_templates_root
        root = _exam_templates_root()
        assert os.path.isdir(root)
        assert "exams" in root
        assert "exports" not in root
        # And there must be a default_v1 inside it.
        assert os.path.isdir(os.path.join(root, "default_v1"))
        # And it must NOT include the shawahid template files.
        for fname in os.listdir(os.path.join(root, "default_v1")):
            assert fname not in ("portfolio.html", "review.html")

    def test_exam_html_does_not_include_shawahid_paths(
        self, base_request, base_profile,
    ):
        exam = generate_exam(base_request, profile=base_profile)
        html = render_exam_html(exam)
        for forbidden in (
            "templates/exports/ministry_v1",
            "ministry_v1/template.html",
            "evidence-card",
        ):
            assert forbidden not in html

    def test_sources_module_documents_safety_rules(self):
        # Phase-11: external providers are real but safe-by-default.
        # They must STILL document the legal/quality rules + the
        # default-disabled posture.
        for module_name in ("base", "madati", "kutubi", "manhaji"):
            module = getattr(sources, module_name)
            doc = (module.__doc__ or "").lower()
            assert ("robots" in doc) or ("legal" in doc) or ("disabledhttpclient" in doc) or ("anti-copy" in doc) or ("safe" in doc), (
                f"{module_name} must document the phase-11 safety rules"
            )
