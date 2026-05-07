"""
curriculum_engine — foundation for Saudi curriculum-aware analysis.

Phase-9 contract
================
This package layers *structural* understanding on top of the
deterministic classifier:

    schemas
        DocumentIntent + DOC_INTENT_* constants,
        AcademicContext + SEMESTER_* constants,
        LearningOutcome, LearningOutcomesBlock

    saudi_curriculum
        Subject, SUBJECTS, STAGE_*, stage_from_label, subject_arabic,
        PLANNING_HEADINGS / ASSESSMENT_HEADINGS / FOLLOWUP_HEADINGS /
        TIMETABLE_HEADINGS / ADMIN_HEADINGS / CERTIFICATE_HEADINGS

    document_intent
        detect_document_intent — fixes "خطة تنفيذ أسبوعية" being
        misclassified as "تنفيذ داخل الصف".

    academic_calendar
        SEMESTER_WEEK_COUNT, infer_semester_from_date,
        infer_academic_year, infer_semester_from_text,
        infer_academic_year_from_text

    week_detector
        detect_week — extract the week number from text.

    semester_detector
        SemesterDetection, detect_semester

    learning_outcomes
        extract_learning_outcomes

Exclusion list (phase rule):
    - No imports from export_engine.
    - No imports from media_engine.
    - No imports from review_engine.
    - No imports from storage_engine.
    - No imports from Playwright.
    - No DB / SQLAlchemy.
    - No GPT / OpenAI.
"""
from __future__ import annotations

from app.curriculum_engine import (
    academic_calendar,
    document_intent,
    learning_outcomes,
    saudi_curriculum,
    semester_detector,
    week_detector,
)
from app.curriculum_engine.academic_calendar import (
    SEMESTER_WEEK_COUNT,
    CalendarHint,
    infer_academic_year,
    infer_academic_year_from_text,
    infer_semester_from_date,
    infer_semester_from_text,
)
from app.curriculum_engine.document_intent import detect_document_intent
from app.curriculum_engine.learning_outcomes import extract_learning_outcomes
from app.curriculum_engine.saudi_curriculum import (
    ADMIN_HEADINGS,
    ASSESSMENT_HEADINGS,
    CERTIFICATE_HEADINGS,
    FOLLOWUP_HEADINGS,
    PLANNING_HEADINGS,
    STAGE_INTERMEDIATE,
    STAGE_KG,
    STAGE_LABELS,
    STAGE_PRIMARY,
    STAGE_SECONDARY,
    SUBJECTS,
    Subject,
    TIMETABLE_HEADINGS,
    stage_from_label,
    subject_arabic,
)
from app.curriculum_engine.schemas import (
    DOC_INTENT_ADMIN,
    DOC_INTENT_ASSESSMENT,
    DOC_INTENT_CERTIFICATE,
    DOC_INTENT_FOLLOWUP,
    DOC_INTENT_IN_CLASS,
    DOC_INTENT_PLANNING,
    DOC_INTENT_RESOURCE,
    DOC_INTENT_TIMETABLE,
    DOC_INTENT_UNKNOWN,
    INTENT_TO_CATEGORY,
    SEMESTER_FIRST,
    SEMESTER_SECOND,
    SEMESTER_THIRD,
    SEMESTER_UNKNOWN,
    AcademicContext,
    DocumentIntent,
    LearningOutcome,
    LearningOutcomesBlock,
)
from app.curriculum_engine.semester_detector import SemesterDetection, detect_semester
from app.curriculum_engine.week_detector import detect_week

__all__ = [
    # submodules (for tests / introspection)
    "academic_calendar",
    "document_intent",
    "learning_outcomes",
    "saudi_curriculum",
    "semester_detector",
    "week_detector",
    # schemas
    "DocumentIntent",
    "AcademicContext",
    "LearningOutcome",
    "LearningOutcomesBlock",
    "DOC_INTENT_PLANNING",
    "DOC_INTENT_IN_CLASS",
    "DOC_INTENT_ASSESSMENT",
    "DOC_INTENT_FOLLOWUP",
    "DOC_INTENT_TIMETABLE",
    "DOC_INTENT_ADMIN",
    "DOC_INTENT_RESOURCE",
    "DOC_INTENT_CERTIFICATE",
    "DOC_INTENT_UNKNOWN",
    "INTENT_TO_CATEGORY",
    "SEMESTER_FIRST",
    "SEMESTER_SECOND",
    "SEMESTER_THIRD",
    "SEMESTER_UNKNOWN",
    # saudi_curriculum
    "Subject",
    "SUBJECTS",
    "STAGE_PRIMARY",
    "STAGE_INTERMEDIATE",
    "STAGE_SECONDARY",
    "STAGE_KG",
    "STAGE_LABELS",
    "stage_from_label",
    "subject_arabic",
    "PLANNING_HEADINGS",
    "ASSESSMENT_HEADINGS",
    "FOLLOWUP_HEADINGS",
    "TIMETABLE_HEADINGS",
    "ADMIN_HEADINGS",
    "CERTIFICATE_HEADINGS",
    # document_intent
    "detect_document_intent",
    # academic_calendar
    "SEMESTER_WEEK_COUNT",
    "CalendarHint",
    "infer_semester_from_date",
    "infer_academic_year",
    "infer_semester_from_text",
    "infer_academic_year_from_text",
    # week_detector
    "detect_week",
    # semester_detector
    "SemesterDetection",
    "detect_semester",
    # learning_outcomes
    "extract_learning_outcomes",
]
