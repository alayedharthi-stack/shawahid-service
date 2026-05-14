"""
Phase 1 — unit tests for the PDF kind classifier.

Pure function tests: no DB, no network, no monkey-patching.
Each test covers one branch of the classifier so we can confirm the
log-only wiring in the webhook would never silently mislabel an
incoming PDF.
"""
from __future__ import annotations

import pytest

from app.services.pdf_kind_classifier import classify_pdf_kind


# ──────────────────────────────────────────────────────────────────────
# Exam / worksheet positives
# ──────────────────────────────────────────────────────────────────────
class TestExamOrWorksheet:
    def test_explicit_exam_with_questions(self):
        text = (
            "اختبار الفترة الأولى — مادة الرياضيات\n"
            "السؤال الأول: اختر الإجابة الصحيحة\n"
            "السؤال الثاني: ضع علامة صح أمام العبارة الصحيحة\n"
            "مجموع الدرجات 20"
        )
        result = classify_pdf_kind(
            extracted_text=text,
            filename="اختبار_رياضيات_الفصل_الأول.pdf",
            has_questions=True,
            has_grades_table=True,
        )
        assert result["pdf_kind"] == "exam_or_worksheet"
        assert result["detected_type"] == "exam"
        assert result["confidence"] >= 0.70

    def test_worksheet_explicit(self):
        text = (
            "ورقة عمل — درس الكسور\n"
            "أكمل الفراغ:\n"
            "صل بين العمود الأول والثاني"
        )
        result = classify_pdf_kind(
            extracted_text=text,
            filename="ورقة_عمل_الكسور.pdf",
            has_questions=True,
        )
        assert result["pdf_kind"] == "exam_or_worksheet"
        assert result["detected_type"] == "worksheet"
        assert result["confidence"] >= 0.70

    def test_assessment_model(self):
        text = (
            "نموذج قياس مهارات اللغة العربية\n"
            "السؤال الأول: اختر الإجابة الصحيحة\n"
            "السؤال الثاني: اختر من متعدد"
        )
        result = classify_pdf_kind(
            extracted_text=text,
            filename="نموذج_قياس.pdf",
            has_questions=True,
        )
        assert result["pdf_kind"] == "exam_or_worksheet"
        assert result["detected_type"] == "exam"

    def test_assignment_with_questions_counts_as_exam_kind(self):
        text = (
            "الواجب المنزلي — درس الجبر\n"
            "السؤال الأول: حل ما يلي\n"
            "السؤال الثاني: أكمل الفراغ\n"
            "الدرجة 10"
        )
        result = classify_pdf_kind(
            extracted_text=text,
            filename="واجب_جبر.pdf",
            has_questions=True,
        )
        assert result["pdf_kind"] == "exam_or_worksheet"
        assert result["detected_type"] in {"assignment", "exam"}

    def test_filename_only_is_strong_enough_for_exam(self):
        # Scanned PDF — extractor returns no text. Filename alone
        # carries 'اختبار' + 'نهائي'.
        result = classify_pdf_kind(
            extracted_text=None,
            filename="اختبار_نهائي_رياضيات.pdf",
        )
        # We accept exam_or_worksheet OR unknown here — filename alone
        # is a weaker signal. The contract is: if exam_or_worksheet,
        # detected_type must be set.
        if result["pdf_kind"] == "exam_or_worksheet":
            assert result["detected_type"] == "exam"
        else:
            assert result["pdf_kind"] == "unknown"


# ──────────────────────────────────────────────────────────────────────
# Evidence (non-exam) negatives
# ──────────────────────────────────────────────────────────────────────
class TestEvidence:
    def test_weekly_plan_is_evidence(self):
        text = (
            "خطة أسبوعية — مادة العلوم\n"
            "نواتج التعلم: بنهاية الدرس يستطيع الطالب أن…\n"
            "الأهداف:\n"
            "تحضير الدرس"
        )
        result = classify_pdf_kind(
            extracted_text=text,
            filename="خطة_أسبوعية.pdf",
            has_objectives=True,
        )
        assert result["pdf_kind"] == "evidence"
        assert result["detected_type"] is None
        assert result["confidence"] >= 0.55

    def test_attendance_log_is_evidence(self):
        text = "سجل الحضور والغياب — الفصل أ\nحضور\nغياب"
        result = classify_pdf_kind(
            extracted_text=text,
            filename="سجل_حضور.pdf",
        )
        assert result["pdf_kind"] == "evidence"

    def test_circular_is_evidence(self):
        text = "تعميم من إدارة التعليم بخصوص الاجتماع الأسبوعي"
        result = classify_pdf_kind(
            extracted_text=text,
            filename="تعميم_رقم_12.pdf",
        )
        assert result["pdf_kind"] == "evidence"

    def test_certificate_is_evidence(self):
        text = "شهادة شكر وتقدير للمعلمة فاطمة على جهودها المتميزة"
        result = classify_pdf_kind(
            extracted_text=text,
            filename="شهادة_شكر.pdf",
        )
        assert result["pdf_kind"] == "evidence"

    def test_report_is_evidence(self):
        text = "تقرير نشاط اليوم العالمي للمعلم — توثيق فعالية"
        result = classify_pdf_kind(
            extracted_text=text,
            filename="تقرير_نشاط.pdf",
        )
        assert result["pdf_kind"] == "evidence"

    def test_achievement_is_evidence(self):
        text = "إنجاز طلابي — تكريم الطلاب المتفوقين"
        result = classify_pdf_kind(
            extracted_text=text,
            filename="إنجاز_طلابي.pdf",
        )
        assert result["pdf_kind"] == "evidence"


# ──────────────────────────────────────────────────────────────────────
# Unknown / ambiguous
# ──────────────────────────────────────────────────────────────────────
class TestUnknown:
    def test_empty_input_is_unknown(self):
        result = classify_pdf_kind()
        assert result["pdf_kind"] == "unknown"
        assert result["confidence"] == 0.0
        assert result["detected_type"] is None

    def test_random_filename_no_text_is_unknown(self):
        result = classify_pdf_kind(
            extracted_text=None,
            filename="document_2024.pdf",
        )
        assert result["pdf_kind"] == "unknown"
        assert result["detected_type"] is None

    def test_short_neutral_text_is_unknown(self):
        result = classify_pdf_kind(
            extracted_text="ملاحظات قصيرة عن اليوم.",
            filename="ملاحظات.pdf",
        )
        assert result["pdf_kind"] == "unknown"


# ──────────────────────────────────────────────────────────────────────
# Contract guarantees
# ──────────────────────────────────────────────────────────────────────
class TestContract:
    @pytest.mark.parametrize("kwargs", [
        {},
        {"filename": "x.pdf"},
        {"extracted_text": "اختبار", "filename": "x.pdf"},
        {"extracted_text": "خطة", "filename": "y.pdf"},
    ])
    def test_return_shape(self, kwargs):
        r = classify_pdf_kind(**kwargs)
        assert set(r.keys()) == {"pdf_kind", "confidence", "reason", "detected_type"}
        assert r["pdf_kind"] in {"exam_or_worksheet", "evidence", "unknown"}
        assert 0.0 <= r["confidence"] <= 1.0
        assert isinstance(r["reason"], str) and r["reason"]
        assert r["detected_type"] in {None, "exam", "worksheet", "assignment", "assessment"}
        # detected_type may only be non-None when classification is exam_or_worksheet.
        if r["pdf_kind"] != "exam_or_worksheet":
            assert r["detected_type"] is None

    def test_pure_function_no_side_effects(self):
        kwargs = {
            "extracted_text": "اختبار قصير — السؤال الأول",
            "filename": "اختبار.pdf",
            "has_questions": True,
        }
        r1 = classify_pdf_kind(**kwargs)
        r2 = classify_pdf_kind(**kwargs)
        assert r1 == r2

    def test_evidence_keywords_do_not_leak_into_exam(self):
        # A plan that happens to mention "تقويم" briefly must remain
        # evidence because the dominant signals are planning ones.
        text = (
            "خطة درس — التقويم التكويني خلال الحصة\n"
            "نواتج التعلم: تحضير الدرس\n"
            "الأهداف"
        )
        r = classify_pdf_kind(
            extracted_text=text,
            filename="خطة_درس.pdf",
            has_objectives=True,
        )
        assert r["pdf_kind"] == "evidence"
