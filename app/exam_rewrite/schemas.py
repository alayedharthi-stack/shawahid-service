"""
exam_rewrite.schemas — frozen DTOs for the structured exam object.

These dataclasses are the *only* surface other layers should see.
They are:
    • Immutable (``frozen=True``) so consumers can safely share them.
    • Plain Python — no pydantic, no SQLAlchemy, no DB coupling.
    • JSON-serialisable via ``to_dict()`` exactly as Phase-3 spec'd.

Question / exam type constants are exported so callers don't sprinkle
string literals around the codebase.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ──────────────────────────────────────────────────────────────────────
# Type constants
# ──────────────────────────────────────────────────────────────────────


QUESTION_TYPE_MULTIPLE_CHOICE = "multiple_choice"
QUESTION_TYPE_TRUE_FALSE      = "true_false"
QUESTION_TYPE_SHORT_ANSWER    = "short_answer"
QUESTION_TYPE_MATCHING        = "matching"
QUESTION_TYPE_COMPLETE        = "complete"
QUESTION_TYPE_ESSAY           = "essay"
QUESTION_TYPE_UNKNOWN         = "unknown"

VALID_QUESTION_TYPES: frozenset[str] = frozenset({
    QUESTION_TYPE_MULTIPLE_CHOICE,
    QUESTION_TYPE_TRUE_FALSE,
    QUESTION_TYPE_SHORT_ANSWER,
    QUESTION_TYPE_MATCHING,
    QUESTION_TYPE_COMPLETE,
    QUESTION_TYPE_ESSAY,
    QUESTION_TYPE_UNKNOWN,
})


EXAM_TYPE_EXAM       = "exam"
EXAM_TYPE_WORKSHEET  = "worksheet"
EXAM_TYPE_ASSIGNMENT = "assignment"
EXAM_TYPE_ASSESSMENT = "assessment"
EXAM_TYPE_UNKNOWN    = "unknown"

VALID_EXAM_TYPES: frozenset[str] = frozenset({
    EXAM_TYPE_EXAM,
    EXAM_TYPE_WORKSHEET,
    EXAM_TYPE_ASSIGNMENT,
    EXAM_TYPE_ASSESSMENT,
    EXAM_TYPE_UNKNOWN,
})


# ──────────────────────────────────────────────────────────────────────
# DTOs
# ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ExamQuestion:
    """A single parsed question.

    ``number`` is the 1-based question number as detected in the
    source. ``type`` is one of the ``QUESTION_TYPE_*`` constants.
    ``score`` is in whatever scoring unit the exam uses (drajat /
    points / ½ marks) — we keep it as a float when present, ``None``
    when the source doesn't expose it explicitly.
    """

    number: int
    type: str = QUESTION_TYPE_UNKNOWN
    text: str = ""
    choices: tuple[str, ...] = ()
    score: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": int(self.number),
            "type": self.type,
            "text": self.text,
            "choices": list(self.choices),
            "score": self.score,
        }


@dataclass(frozen=True)
class ExamSection:
    """A group of questions sharing a heading (e.g. السؤال الأول).

    For exams without explicit sectioning we still produce a single
    ``ExamSection`` so downstream renderers don't have to handle two
    shapes.
    """

    title: str | None = None
    score: float | None = None
    questions: tuple[ExamQuestion, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "score": self.score,
            "questions": [q.to_dict() for q in self.questions],
        }


@dataclass(frozen=True)
class StructuredExam:
    """The complete analysis result returned by ``analyze_exam_pdf``.

    The shape mirrors Phase-3 spec exactly so callers can round-trip
    through ``to_dict()`` / ``json.dumps`` without bespoke wrappers.

    Warnings are short Arabic strings describing analysis gaps — used
    by the webhook to decide whether to send the success or failure
    Arabic reply.
    """

    subject: str | None = None
    grade: str | None = None
    exam_type: str = EXAM_TYPE_EXAM
    title: str | None = None
    instructions: str | None = None
    total_score: float | None = None
    sections: tuple[ExamSection, ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)

    # ── Helpers ──────────────────────────────────────────────────────

    @property
    def total_questions(self) -> int:
        return sum(len(s.questions) for s in self.sections)

    def is_usable(self) -> bool:
        """True when we found enough to attempt a rewrite later.

        Phase-3 contract: at least one parsed question. The webhook
        uses this to decide between the success reply and the
        "couldn't analyse" apology.
        """
        return self.total_questions >= 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "grade": self.grade,
            "exam_type": self.exam_type,
            "title": self.title,
            "instructions": self.instructions,
            "total_score": self.total_score,
            "sections": [s.to_dict() for s in self.sections],
            "warnings": list(self.warnings),
        }


__all__ = [
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
    "VALID_QUESTION_TYPES",
    "EXAM_TYPE_EXAM",
    "EXAM_TYPE_WORKSHEET",
    "EXAM_TYPE_ASSIGNMENT",
    "EXAM_TYPE_ASSESSMENT",
    "EXAM_TYPE_UNKNOWN",
    "VALID_EXAM_TYPES",
]
