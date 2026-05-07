"""
curriculum_engine.academic_calendar — Saudi academic-year boundaries.

Foundation only — phase-9 promise. We *don't* try to map a Gregorian
date to a Saudi MOE Hijri week (the official calendar shifts each
year and would need an authoritative table). Instead, this module
provides the building blocks downstream code (and humans) can lean on:

    • semester names + their typical week counts
    • a helper to format an academic year string
    • a tiny ``infer_semester_from_text`` for cases where the document
      itself names the semester

Pure module. No DB / network.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from app.curriculum_engine.schemas import (
    SEMESTER_FIRST,
    SEMESTER_SECOND,
    SEMESTER_THIRD,
    SEMESTER_UNKNOWN,
)
from app.services.intents import normalize


# Typical week counts per semester in the Saudi MOE 3-term system.
SEMESTER_WEEK_COUNT: dict[str, int] = {
    SEMESTER_FIRST: 13,
    SEMESTER_SECOND: 13,
    SEMESTER_THIRD: 12,
}

# Heuristic month windows (Gregorian) for each semester. These are
# *advisory* — the official calendar shifts year to year.
_SEMESTER_MONTH_WINDOWS: dict[str, tuple[int, int]] = {
    SEMESTER_FIRST: (8, 11),     # Aug-Nov
    SEMESTER_SECOND: (12, 2),    # Dec-Feb
    SEMESTER_THIRD: (3, 6),      # Mar-Jun
}


@dataclass(frozen=True)
class CalendarHint:
    """An advisory placement of a date inside the academic year."""

    semester: str = SEMESTER_UNKNOWN
    academic_year: str | None = None


def infer_semester_from_date(date: datetime | None = None) -> str:
    """Best-effort semester inference from a Gregorian month."""
    if date is None:
        return SEMESTER_UNKNOWN
    month = date.month
    for semester, (start, end) in _SEMESTER_MONTH_WINDOWS.items():
        if start <= end:
            if start <= month <= end:
                return semester
        else:  # window wraps year boundary (Dec-Feb)
            if month >= start or month <= end:
                return semester
    return SEMESTER_UNKNOWN


def infer_academic_year(date: datetime | None = None) -> str | None:
    """Format the academic year as e.g. ``"1446-1447هـ"``.

    We don't have an embedded Hijri table, so we approximate with the
    Gregorian year and an annotation. Callers that need exact Hijri
    should plug in their own resolver.
    """
    if date is None:
        return None
    g = date.year
    # School year usually starts in autumn — Aug onward kicks the new year.
    if date.month >= 8:
        return f"{g}-{g + 1}م"
    return f"{g - 1}-{g}م"


# Patterns the document itself sometimes spells out.
_FIRST_RE = re.compile(r"الفصل\s*الدراسي\s*الاول")
_SECOND_RE = re.compile(r"الفصل\s*الدراسي\s*الثاني")
_THIRD_RE = re.compile(r"الفصل\s*الدراسي\s*الثالث")


def infer_semester_from_text(text: str | None) -> str:
    if not text:
        return SEMESTER_UNKNOWN
    norm = normalize(text)
    if _THIRD_RE.search(norm):
        return SEMESTER_THIRD
    if _SECOND_RE.search(norm):
        return SEMESTER_SECOND
    if _FIRST_RE.search(norm):
        return SEMESTER_FIRST
    return SEMESTER_UNKNOWN


_HIJRI_YEAR_RE = re.compile(r"\b(14\d\d|15\d\d)\s*ه")


def infer_academic_year_from_text(text: str | None) -> str | None:
    if not text:
        return None
    norm = normalize(text)
    m = _HIJRI_YEAR_RE.search(norm)
    if not m:
        return None
    year = m.group(1)
    return f"{year}هـ"


__all__ = [
    "SEMESTER_WEEK_COUNT",
    "CalendarHint",
    "infer_semester_from_date",
    "infer_academic_year",
    "infer_semester_from_text",
    "infer_academic_year_from_text",
]
