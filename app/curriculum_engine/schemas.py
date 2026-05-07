"""
curriculum_engine.schemas — DTOs for curriculum-aware analysis.

Pure dataclasses. No DB / GPT / Playwright / network.

These DTOs decouple the curriculum analysis from the existing
``classification.py`` so the structural intent layer can mature
independently. Phase-9 promise: this package adds *signals*, never
overwrites the deterministic classifier output.
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ──────────────────────────────────────────────────────────────────────
# Document intent
# ──────────────────────────────────────────────────────────────────────

# Coarse "what is this document trying to be" labels. These intentionally
# diverge from the export categories — a planning document can flow into
# the export as "التخطيط" but its *intent* is more specific (e.g. weekly
# vs term plan).
DOC_INTENT_PLANNING = "planning"            # خطة (أسبوعية / فصلية / يومية)
DOC_INTENT_IN_CLASS = "in_class"            # تنفيذ داخل الصف / نشاط
DOC_INTENT_ASSESSMENT = "assessment"        # اختبار / ورقة عمل / كشف درجات
DOC_INTENT_FOLLOWUP = "followup"            # سجل متابعة / حضور
DOC_INTENT_TIMETABLE = "timetable"          # جدول حصص
DOC_INTENT_ADMIN = "admin"                  # تعميم / خطاب
DOC_INTENT_RESOURCE = "resource"            # مصدر إثرائي
DOC_INTENT_CERTIFICATE = "certificate"      # شهادة دورة
DOC_INTENT_UNKNOWN = "unknown"


# Map a document intent → the export category it should land in.
INTENT_TO_CATEGORY: dict[str, str] = {
    DOC_INTENT_PLANNING: "التخطيط",
    DOC_INTENT_IN_CLASS: "نشاط صفي",
    DOC_INTENT_ASSESSMENT: "التقويم",
    DOC_INTENT_FOLLOWUP: "سجل المتابعة",
    DOC_INTENT_TIMETABLE: "ملفات إدارية",
    DOC_INTENT_ADMIN: "ملفات إدارية",
    DOC_INTENT_RESOURCE: "مصدر تعليمي",
    DOC_INTENT_CERTIFICATE: "الدورات والشهادات",
    DOC_INTENT_UNKNOWN: "أخرى",
}


@dataclass(frozen=True)
class DocumentIntent:
    """Structural intent of a document.

    ``intent`` is one of the ``DOC_INTENT_*`` constants.
    ``confidence`` is 0-1.
    ``signals`` lists the structural cues that contributed to the score
    (e.g. ``("heading:نواتج التعلم", "section:التهيئة")``).
    """

    intent: str
    confidence: float
    signals: tuple[str, ...] = ()
    reason: str = ""

    @property
    def export_category(self) -> str:
        return INTENT_TO_CATEGORY.get(self.intent, INTENT_TO_CATEGORY[DOC_INTENT_UNKNOWN])


# ──────────────────────────────────────────────────────────────────────
# Academic calendar
# ──────────────────────────────────────────────────────────────────────

SEMESTER_FIRST = "first"
SEMESTER_SECOND = "second"
SEMESTER_THIRD = "third"
SEMESTER_UNKNOWN = "unknown"


@dataclass(frozen=True)
class AcademicContext:
    """High-level placement of a document in the academic year.

    Every field is optional — partial knowledge is still useful.
    """

    semester: str = SEMESTER_UNKNOWN
    academic_year: str | None = None  # e.g. "1446هـ" / "1446-1447هـ"
    current_week: int | None = None   # 1..N within the semester
    unit: str | None = None           # e.g. "الوحدة الأولى"

    def is_known(self) -> bool:
        return (
            self.semester != SEMESTER_UNKNOWN
            or self.academic_year is not None
            or self.current_week is not None
            or self.unit is not None
        )


# ──────────────────────────────────────────────────────────────────────
# Learning outcomes
# ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LearningOutcome:
    """A single learning outcome extracted from a document.

    ``raw`` keeps the original phrasing for export rendering.
    ``bloom_level`` is one of (``knowledge``, ``comprehension``,
    ``application``, ``analysis``, ``synthesis``, ``evaluation``) when
    we can infer it from the verb used. ``None`` otherwise.
    """

    raw: str
    bloom_level: str | None = None
    verb: str | None = None


@dataclass(frozen=True)
class LearningOutcomesBlock:
    """The full set of outcomes detected in one document."""

    outcomes: tuple[LearningOutcome, ...] = ()
    confidence: float = 0.0
    reason: str = ""

    @property
    def count(self) -> int:
        return len(self.outcomes)


__all__ = [
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
    "DocumentIntent",
    "SEMESTER_FIRST",
    "SEMESTER_SECOND",
    "SEMESTER_THIRD",
    "SEMESTER_UNKNOWN",
    "AcademicContext",
    "LearningOutcome",
    "LearningOutcomesBlock",
]
