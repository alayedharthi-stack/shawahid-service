"""
curriculum_engine.semester_detector — detect which semester a document
belongs to.

Convenience layer on top of ``academic_calendar``. Returns one of the
``SEMESTER_*`` constants from ``schemas`` and a confidence score.

Pure module.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.curriculum_engine.academic_calendar import (
    infer_semester_from_date,
    infer_semester_from_text,
)
from app.curriculum_engine.schemas import (
    SEMESTER_FIRST,
    SEMESTER_SECOND,
    SEMESTER_THIRD,
    SEMESTER_UNKNOWN,
)


@dataclass(frozen=True)
class SemesterDetection:
    semester: str
    confidence: float
    reason: str


def detect_semester(
    *,
    text: str | None = None,
    fallback_date: datetime | None = None,
) -> SemesterDetection:
    """Pick the most likely semester for a document.

    Order of preference:
        1. Explicit textual mention ("الفصل الدراسي الثاني").
        2. Date heuristic (Gregorian month window).
        3. Unknown.
    """
    from_text = infer_semester_from_text(text)
    if from_text != SEMESTER_UNKNOWN:
        return SemesterDetection(
            semester=from_text,
            confidence=0.95,
            reason="text:explicit",
        )

    if fallback_date is not None:
        from_date = infer_semester_from_date(fallback_date)
        if from_date != SEMESTER_UNKNOWN:
            return SemesterDetection(
                semester=from_date,
                confidence=0.6,
                reason="date:month-window",
            )

    return SemesterDetection(
        semester=SEMESTER_UNKNOWN,
        confidence=0.0,
        reason="no signals",
    )


__all__ = [
    "SemesterDetection",
    "detect_semester",
    "SEMESTER_FIRST",
    "SEMESTER_SECOND",
    "SEMESTER_THIRD",
    "SEMESTER_UNKNOWN",
]
