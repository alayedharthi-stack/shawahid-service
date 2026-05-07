"""
exam_engine.schemas — DTOs for the exam-generation pipeline.

Phase-10 contract
=================
Pure dataclasses. No DB / GPT / network / Playwright / file I/O.

These DTOs decouple the exam pipeline from the shawahid (evidence)
pipeline. Nothing here imports from export_engine, media_engine,
review_engine or storage_engine.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


# ──────────────────────────────────────────────────────────────────────
# Question types
# ──────────────────────────────────────────────────────────────────────

QTYPE_MCQ = "mcq"               # اختيار من متعدد
QTYPE_TRUE_FALSE = "true_false" # صح/خطأ
QTYPE_FILL_BLANK = "fill_blank" # أكمل
QTYPE_SHORT = "short"           # سؤال قصير
QTYPE_MATCH = "match"           # مطابقة

QTYPES_ALL = (
    QTYPE_MCQ,
    QTYPE_TRUE_FALSE,
    QTYPE_FILL_BLANK,
    QTYPE_SHORT,
    QTYPE_MATCH,
)

QTYPE_LABELS_AR: dict[str, str] = {
    QTYPE_MCQ: "اختيار من متعدد",
    QTYPE_TRUE_FALSE: "صح أو خطأ",
    QTYPE_FILL_BLANK: "أكمل الفراغ",
    QTYPE_SHORT: "سؤال قصير",
    QTYPE_MATCH: "مطابقة",
}


# ──────────────────────────────────────────────────────────────────────
# Exam types
# ──────────────────────────────────────────────────────────────────────

EXAM_TYPE_QUICK = "quick"           # اختبار قصير
EXAM_TYPE_MONTHLY = "monthly"       # اختبار شهري
EXAM_TYPE_FINAL = "final"           # اختبار نهائي
EXAM_TYPE_PRACTICAL = "practical"   # اختبار عملي
EXAM_TYPE_QIYAS = "qiyas"           # ورقة قياس
EXAM_TYPE_HOMEWORK = "homework"     # واجب قصير

EXAM_TYPES_ALL = (
    EXAM_TYPE_QUICK,
    EXAM_TYPE_MONTHLY,
    EXAM_TYPE_FINAL,
    EXAM_TYPE_PRACTICAL,
    EXAM_TYPE_QIYAS,
    EXAM_TYPE_HOMEWORK,
)

EXAM_TYPE_LABELS_AR: dict[str, str] = {
    EXAM_TYPE_QUICK: "اختبار قصير",
    EXAM_TYPE_MONTHLY: "اختبار شهري",
    EXAM_TYPE_FINAL: "اختبار نهائي",
    EXAM_TYPE_PRACTICAL: "اختبار عملي",
    EXAM_TYPE_QIYAS: "ورقة قياس",
    EXAM_TYPE_HOMEWORK: "واجب قصير",
}


# ──────────────────────────────────────────────────────────────────────
# Source modes — where the questions come from
# ──────────────────────────────────────────────────────────────────────

SOURCE_TEACHER_FILE = "from_teacher_file"
SOURCE_CURRICULUM = "from_curriculum_context"
SOURCE_SAMPLE_BANK = "from_sample_bank"
SOURCE_MANUAL_TOPIC = "manual_topic"

SOURCE_MODES_ALL = (
    SOURCE_TEACHER_FILE,
    SOURCE_CURRICULUM,
    SOURCE_SAMPLE_BANK,
    SOURCE_MANUAL_TOPIC,
)


# ──────────────────────────────────────────────────────────────────────
# Profile (header data printed on every exam)
# ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ExamProfile:
    """Header / footer data stamped on every generated exam.

    All fields are optional — the renderer falls back to placeholders
    (e.g. "............") for missing values so the printed sheet
    always looks complete.
    """

    teacher_name: str | None = None
    school_name: str | None = None
    education_admin: str | None = None  # إدارة التعليم
    region: str | None = None           # المنطقة / المحافظة
    country: str = "المملكة العربية السعودية"
    ministry: str = "وزارة التعليم"
    subject: str | None = None
    grade: str | None = None             # e.g. "الصف الرابع"
    stage: str | None = None             # المرحلة الابتدائية / المتوسطة / الثانوية
    semester: str | None = None          # الفصل الدراسي الأول / الثاني / الثالث
    academic_year: str | None = None
    exam_type: str = EXAM_TYPE_QUICK
    duration_minutes: int = 30
    total_marks: int = 20

    def exam_type_label(self) -> str:
        return EXAM_TYPE_LABELS_AR.get(self.exam_type, self.exam_type)


# ──────────────────────────────────────────────────────────────────────
# Question
# ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ExamQuestion:
    """A single exam question.

    The renderer treats ``choices`` as ordered. ``correct_answer`` is
    either an index (for MCQ), a literal string (for fill-blank /
    true-false), or a list of (left, right) pairs (for match).
    """

    id: str
    type: str
    text: str
    choices: tuple[str, ...] = ()
    correct_answer: str | int | tuple = ""
    marks: float = 1.0
    difficulty: str = "medium"  # easy / medium / hard
    learning_outcome: str | None = None
    bloom_level: str | None = None

    @staticmethod
    def new_id() -> str:
        return f"q-{uuid.uuid4().hex[:8]}"

    def is_objective(self) -> bool:
        """Objective questions can be auto-graded; subjective ones can't."""
        return self.type in (QTYPE_MCQ, QTYPE_TRUE_FALSE, QTYPE_MATCH)


# ──────────────────────────────────────────────────────────────────────
# Request
# ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ExamRequest:
    """A teacher's request for a new exam.

    ``unit`` and ``lesson`` are free-text labels. ``question_types`` is
    the set the teacher wants to include; the generator will fall back
    to MCQ if the set is empty or impossible to satisfy.
    """

    teacher_id: int
    exam_type: str = EXAM_TYPE_QUICK
    subject: str | None = None
    grade: str | None = None
    stage: str | None = None
    semester: str | None = None
    unit: str | None = None
    lesson: str | None = None
    week: int | None = None
    difficulty: str = "medium"
    question_types: tuple[str, ...] = (QTYPE_MCQ, QTYPE_TRUE_FALSE)
    total_questions: int = 10
    total_marks: int = 20
    duration_minutes: int = 30
    source_mode: str = SOURCE_MANUAL_TOPIC
    topic: str | None = None  # used when source_mode is manual_topic

    def required_fields_missing(self) -> tuple[str, ...]:
        """Return a tuple of minimum-required fields not yet supplied.

        The webhook builds the "missing info" message from this list
        so the prompt only asks about gaps the teacher actually has.
        """
        missing: list[str] = []
        if not self.subject:
            missing.append("subject")
        if not self.grade and not self.stage:
            missing.append("grade")
        if not self.exam_type:
            missing.append("exam_type")
        if (
            self.source_mode == SOURCE_MANUAL_TOPIC
            and not self.topic
            and not self.lesson
            and not self.unit
        ):
            missing.append("topic")
        return tuple(missing)


# ──────────────────────────────────────────────────────────────────────
# Generated exam
# ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GeneratedExam:
    """The output of ``exam_generator.generate_exam``.

    ``warnings`` carries non-fatal issues the generator wants the
    validator / teacher to be aware of (e.g. "fewer questions than
    requested — sample bank exhausted").
    """

    profile: ExamProfile
    questions: tuple[ExamQuestion, ...]
    request: ExamRequest
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    warnings: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    exam_id: str = field(default_factory=lambda: f"ex-{uuid.uuid4().hex[:10]}")

    @property
    def question_count(self) -> int:
        return len(self.questions)

    @property
    def actual_total_marks(self) -> float:
        return round(sum(q.marks for q in self.questions), 2)


# ──────────────────────────────────────────────────────────────────────
# Validation result
# ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ValidationIssue:
    """A single problem with a generated exam."""

    code: str
    message: str
    question_id: str | None = None
    severity: str = "error"  # error / warning


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of running ``exam_validator.validate_exam``."""

    issues: tuple[ValidationIssue, ...] = ()

    @property
    def is_valid(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        return tuple(i for i in self.issues if i.severity == "error")

    @property
    def warnings(self) -> tuple[ValidationIssue, ...]:
        return tuple(i for i in self.issues if i.severity == "warning")


__all__ = [
    # question types
    "QTYPE_MCQ", "QTYPE_TRUE_FALSE", "QTYPE_FILL_BLANK",
    "QTYPE_SHORT", "QTYPE_MATCH", "QTYPES_ALL", "QTYPE_LABELS_AR",
    # exam types
    "EXAM_TYPE_QUICK", "EXAM_TYPE_MONTHLY", "EXAM_TYPE_FINAL",
    "EXAM_TYPE_PRACTICAL", "EXAM_TYPE_QIYAS", "EXAM_TYPE_HOMEWORK",
    "EXAM_TYPES_ALL", "EXAM_TYPE_LABELS_AR",
    # source modes
    "SOURCE_TEACHER_FILE", "SOURCE_CURRICULUM",
    "SOURCE_SAMPLE_BANK", "SOURCE_MANUAL_TOPIC", "SOURCE_MODES_ALL",
    # DTOs
    "ExamProfile", "ExamQuestion", "ExamRequest", "GeneratedExam",
    "ValidationIssue", "ValidationResult",
]
