"""
exam_rewrite — Phase 3 structured exam analysis layer.

Purpose
=======
When a teacher chooses "إعادة صياغة الاختبار" for a PDF, this package
turns the raw PDF into a structured Arabic JSON object describing the
exam (subject, grade, exam type, sections, questions, choices, …).

Hard rules:
    • Pure Python: no GPT call, no DB, no network in Phase 3.
    • No PDF generation, no HTML rendering — those belong to later
      phases.
    • Lives entirely inside ``shawahid-service``. Does NOT import
      from Nahla AI, campaigns, billing, subscriptions, catalog,
      coexistence, 360dialog, customer segmentation, or any
      cross-service shared logic.

Public entry-point:
    ``analyze_exam_pdf(storage_path, ...)`` → :class:`StructuredExam`.
"""
from __future__ import annotations

from app.exam_rewrite.schemas import (
    ExamQuestion,
    ExamSection,
    StructuredExam,
    QUESTION_TYPE_MULTIPLE_CHOICE,
    QUESTION_TYPE_TRUE_FALSE,
    QUESTION_TYPE_SHORT_ANSWER,
    QUESTION_TYPE_MATCHING,
    QUESTION_TYPE_COMPLETE,
    QUESTION_TYPE_ESSAY,
    QUESTION_TYPE_UNKNOWN,
    EXAM_TYPE_EXAM,
    EXAM_TYPE_WORKSHEET,
    EXAM_TYPE_ASSIGNMENT,
    EXAM_TYPE_ASSESSMENT,
    EXAM_TYPE_UNKNOWN,
)
from app.exam_rewrite.flow import analyze_exam_pdf, analyze_exam_text

__all__ = [
    "analyze_exam_pdf",
    "analyze_exam_text",
    "ExamQuestion",
    "ExamSection",
    "StructuredExam",
    "QUESTION_TYPE_MULTIPLE_CHOICE",
    "QUESTION_TYPE_TRUE_FALSE",
    "QUESTION_TYPE_SHORT_ANSWER",
    "QUESTION_TYPE_MATCHING",
    "QUESTION_TYPE_COMPLETE",
    "QUESTION_TYPE_ESSAY",
    "QUESTION_TYPE_UNKNOWN",
    "EXAM_TYPE_EXAM",
    "EXAM_TYPE_WORKSHEET",
    "EXAM_TYPE_ASSIGNMENT",
    "EXAM_TYPE_ASSESSMENT",
    "EXAM_TYPE_UNKNOWN",
]
