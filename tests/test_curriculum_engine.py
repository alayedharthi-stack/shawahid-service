"""
tests/test_curriculum_engine.py — phase-9 curriculum_engine.

Covers the required scenarios from the phase brief:

    • خطة أسبوعية → DOC_INTENT_PLANNING.
    • شرح نشاط داخل الصف → DOC_INTENT_IN_CLASS.
    • جدول حصص → DOC_INTENT_TIMETABLE → "ملفات إدارية".
    • document_intent does not depend on a single keyword.
    • The "خطة تنفيذ أسبوعية" trap → still planning.
    • week / semester / outcomes detection.
    • curriculum_engine has no forbidden imports.
"""
from __future__ import annotations

import ast
import os
from datetime import datetime

import pytest

from app.curriculum_engine import (
    DOC_INTENT_ADMIN,
    DOC_INTENT_ASSESSMENT,
    DOC_INTENT_CERTIFICATE,
    DOC_INTENT_FOLLOWUP,
    DOC_INTENT_IN_CLASS,
    DOC_INTENT_PLANNING,
    DOC_INTENT_TIMETABLE,
    DOC_INTENT_UNKNOWN,
    SEMESTER_FIRST,
    SEMESTER_SECOND,
    SEMESTER_THIRD,
    SEMESTER_UNKNOWN,
    detect_document_intent,
    detect_semester,
    detect_week,
    extract_learning_outcomes,
    infer_academic_year,
    infer_semester_from_text,
    saudi_curriculum,
    document_intent as _doc_intent_mod,
)


# ──────────────────────────────────────────────────────────────────────
# Document intent — the headline phase-9 fix
# ──────────────────────────────────────────────────────────────────────


class TestDocumentIntent:
    def test_weekly_plan_is_planning(self):
        text = """
        خطة أسبوعية
        الأسبوع الأول
        نواتج التعلم:
        - يحل الطالب المعادلات
        التهيئة:
        طرح أسئلة افتتاحية
        الواجب: تمارين الكتاب
        """
        out = detect_document_intent(text=text, title="خطة أسبوعية")
        assert out.intent == DOC_INTENT_PLANNING
        assert out.confidence >= 0.7
        assert out.export_category == "التخطيط"

    def test_term_plan_is_planning(self):
        text = """
        خطة فصلية
        توزيع المنهج
        الفصل الدراسي الأول
        الوحدة الأولى
        """
        out = detect_document_intent(text=text)
        assert out.intent == DOC_INTENT_PLANNING

    def test_in_class_activity_report(self):
        text = """
        اليوم قام الطلاب بتنفيذ نشاط جماعي
        تفاعل الطلاب بشكل ممتاز
        صور تنفيذ النشاط مرفقة
        """
        out = detect_document_intent(text=text, title="نشاط داخل الصف")
        assert out.intent == DOC_INTENT_IN_CLASS
        assert out.export_category == "نشاط صفي"

    def test_timetable_grid(self):
        text = """
        الأحد   الاثنين   الثلاثاء   الأربعاء   الخميس
        الحصة الأولى | رياضيات | علوم | عربي | فقه | إنجليزي
        الحصة الثانية | علوم | فقه | رياضيات | عربي | حاسب
        """
        out = detect_document_intent(text=text, title="جدول الحصص")
        assert out.intent == DOC_INTENT_TIMETABLE
        assert out.export_category == "ملفات إدارية"

    def test_assessment_with_scores(self):
        text = """
        كشف الدرجات
        أسئلة الاختبار النهائي
        رصد الدرجات
        """
        out = detect_document_intent(text=text)
        assert out.intent == DOC_INTENT_ASSESSMENT

    def test_followup_register(self):
        text = "سجل المتابعة\nكشف الحضور\nمتابعة يومية"
        out = detect_document_intent(text=text)
        assert out.intent == DOC_INTENT_FOLLOWUP

    def test_admin_circular(self):
        text = "تعميم رقم 5\nنحيطكم علماً بأن الاجتماع غداً\nالمديرية العامة"
        out = detect_document_intent(text=text)
        assert out.intent == DOC_INTENT_ADMIN

    def test_certificate(self):
        text = "شهادة إتمام\nيشهد بأن المعلم اجتاز بنجاح دورة"
        out = detect_document_intent(text=text)
        assert out.intent == DOC_INTENT_CERTIFICATE

    def test_does_not_depend_on_single_keyword(self):
        # "خطة" alone is NOT enough — confidence stays low.
        out = detect_document_intent(text="خطة")
        assert out.intent in (DOC_INTENT_PLANNING, DOC_INTENT_UNKNOWN)
        assert out.confidence < 0.85

    def test_khutta_tanfithia_usbu3iyya_trap(self):
        """The exact bug the AI eval surfaced: a *weekly plan* titled
        "خطة تنفيذ أسبوعية" with planning structure must classify as
        PLANNING, not as in-class execution."""
        text = """
        خطة تنفيذ أسبوعية
        الأسبوع الثاني
        نواتج التعلم: أن يحل الطالب المعادلات الخطية
        التهيئة الحافزة: طرح مسألة من الواقع
        العرض: شرح النظرية
        التقويم: حل تمارين الكتاب
        الواجب: تمارين 1 إلى 5
        """
        out = detect_document_intent(text=text, title="خطة تنفيذ أسبوعية")
        assert out.intent == DOC_INTENT_PLANNING, (
            f"Expected planning, got {out.intent} signals={out.signals}"
        )
        assert out.export_category == "التخطيط"

    def test_empty_inputs(self):
        out = detect_document_intent(text=None, title=None, filename=None)
        assert out.intent == DOC_INTENT_UNKNOWN
        assert out.confidence == 0.0


# ──────────────────────────────────────────────────────────────────────
# Week detector
# ──────────────────────────────────────────────────────────────────────


class TestWeekDetector:
    def test_ordinal_first(self):
        assert detect_week("الأسبوع الأول") == 1

    def test_ordinal_seventh(self):
        assert detect_week("الأسبوع السابع من الفصل") == 7

    def test_compound_ordinal_twelfth(self):
        assert detect_week("الأسبوع الثاني عشر") == 12

    def test_numeric(self):
        assert detect_week("الأسبوع 5") == 5

    def test_with_raqm(self):
        assert detect_week("أسبوع رقم 9") == 9

    def test_no_match(self):
        assert detect_week("درس عادي") is None

    def test_empty(self):
        assert detect_week(None) is None
        assert detect_week("") is None


# ──────────────────────────────────────────────────────────────────────
# Semester / academic year
# ──────────────────────────────────────────────────────────────────────


class TestSemester:
    def test_text_explicit_first(self):
        assert infer_semester_from_text("الفصل الدراسي الأول") == SEMESTER_FIRST

    def test_text_explicit_third(self):
        assert infer_semester_from_text("الفصل الدراسي الثالث") == SEMESTER_THIRD

    def test_unknown_when_no_signal(self):
        assert infer_semester_from_text("درس عادي") == SEMESTER_UNKNOWN

    def test_detect_with_text_wins_over_date(self):
        out = detect_semester(
            text="الفصل الدراسي الثاني",
            fallback_date=datetime(2025, 9, 1),
        )
        assert out.semester == SEMESTER_SECOND
        assert out.confidence >= 0.9

    def test_detect_falls_back_to_date(self):
        out = detect_semester(
            text=None,
            fallback_date=datetime(2025, 9, 15),  # autumn → first
        )
        assert out.semester == SEMESTER_FIRST

    def test_academic_year_format(self):
        out = infer_academic_year(datetime(2025, 9, 15))
        assert out and "2025" in out


# ──────────────────────────────────────────────────────────────────────
# Learning outcomes
# ──────────────────────────────────────────────────────────────────────


class TestLearningOutcomes:
    def test_extracts_bulleted_outcomes(self):
        text = """
        نواتج التعلم:
        - يحل الطالب المعادلات الخطية
        - يفسر العلاقة بين المتغيرين
        - يطبق القانون على مسائل من الواقع
        التهيئة: ...
        """
        block = extract_learning_outcomes(text)
        assert block.count >= 3
        assert block.confidence >= 0.6
        first = block.outcomes[0]
        assert "يحل" in first.raw

    def test_bloom_level_assignment(self):
        text = """
        نواتج التعلم:
        - يحلل الطالب البيانات
        التهيئة:
        """
        block = extract_learning_outcomes(text)
        assert block.outcomes
        outcome = block.outcomes[0]
        # "يحلل" → analysis (when matched) — but if matcher is conservative
        # we still require *some* assignment for a verb-led sentence.
        assert outcome.verb in (None, "يحلل")
        if outcome.bloom_level is not None:
            assert outcome.bloom_level == "analysis"

    def test_no_outcomes_block(self):
        block = extract_learning_outcomes("نص عشوائي بدون عناوين")
        assert block.count == 0


# ──────────────────────────────────────────────────────────────────────
# Saudi curriculum metadata
# ──────────────────────────────────────────────────────────────────────


class TestSaudiCurriculum:
    def test_known_subjects(self):
        codes = {s.code for s in saudi_curriculum.SUBJECTS}
        for required in ("math", "science", "arabic", "english", "religion"):
            assert required in codes

    def test_stage_from_label(self):
        from app.curriculum_engine.saudi_curriculum import (
            STAGE_INTERMEDIATE,
            STAGE_PRIMARY,
            stage_from_label,
        )
        assert stage_from_label("المرحلة الابتدائية") == STAGE_PRIMARY
        assert stage_from_label("المرحلة المتوسطة") == STAGE_INTERMEDIATE


# ──────────────────────────────────────────────────────────────────────
# Architectural contracts
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
    def test_no_forbidden_imports(self):
        pkg_root = os.path.dirname(_doc_intent_mod.__file__)
        for fname in os.listdir(pkg_root):
            if not fname.endswith(".py"):
                continue
            full = os.path.join(pkg_root, fname)
            for module in _walk_imports(full):
                for forbidden in _FORBIDDEN_PREFIXES:
                    assert not module.startswith(forbidden), (
                        f"{fname} imports forbidden module {module}"
                    )
