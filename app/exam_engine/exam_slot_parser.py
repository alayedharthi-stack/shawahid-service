"""
exam_engine.exam_slot_parser — extract exam slots from free Arabic text.

Phase-12 contract
=================
The teacher writes things like:

    "أريد اختبار نهائي رياضيات للصف الرابع الفصل الثاني"

We pull as many slots as possible in a single pass:

    • subject       (الرياضيات / العلوم / لغتي / ...)
    • grade         (الصف الأول … الثاني عشر)
    • stage         (ابتدائي / متوسط / ثانوي)
    • semester      (الفصل الدراسي الأول / الثاني / الثالث)
    • exam_type     (نهائي / قصير / شهري / عملي / قياس / واجب)
    • unit / lesson (best-effort: "الوحدة الثانية" / "درس الكسور")
    • week          (الأسبوع N)

Pure module. No DB / GPT / network — uses the project's
``services.intents.normalize`` so diacritics / hamza variants don't
break the matchers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.exam_engine.schemas import (
    EXAM_TYPE_FINAL,
    EXAM_TYPE_HOMEWORK,
    EXAM_TYPE_MONTHLY,
    EXAM_TYPE_PRACTICAL,
    EXAM_TYPE_QIYAS,
    EXAM_TYPE_QUICK,
)
from app.services.intents import normalize


# ──────────────────────────────────────────────────────────────────────
# Result DTO
# ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ExamSlots:
    """All slots we managed to pull from one inbound message."""

    subject: str | None = None
    grade: str | None = None
    stage: str | None = None
    semester: str | None = None
    exam_type: str | None = None
    unit: str | None = None
    lesson: str | None = None
    week: int | None = None

    def is_empty(self) -> bool:
        return not any((
            self.subject, self.grade, self.stage, self.semester,
            self.exam_type, self.unit, self.lesson, self.week,
        ))

    def as_kwargs(self) -> dict:
        """Return only non-None slots — convenient for ``state.merge``."""
        return {
            k: v for k, v in {
                "subject": self.subject,
                "grade": self.grade,
                "stage": self.stage,
                "semester": self.semester,
                "exam_type": self.exam_type,
                "unit": self.unit,
                "lesson": self.lesson,
                "week": self.week,
            }.items()
            if v is not None
        }


# ──────────────────────────────────────────────────────────────────────
# Lookup tables
# ──────────────────────────────────────────────────────────────────────

# Subject → canonical Arabic. The matcher operates on the *normalised*
# form (no diacritics / hamza / taa marbuta), so the keys here are
# pre-normalised too.
_SUBJECT_BY_NORM_KEYWORD: tuple[tuple[str, str], ...] = (
    ("الرياضيات", "الرياضيات"),
    ("رياضيات", "الرياضيات"),
    ("العلوم", "العلوم"),
    ("علوم", "العلوم"),
    ("الفيزياء", "الفيزياء"),
    ("فيزياء", "الفيزياء"),
    ("الكيمياء", "الكيمياء"),
    ("كيمياء", "الكيمياء"),
    ("الاحياء", "الأحياء"),
    ("احياء", "الأحياء"),
    ("لغتي", "لغتي"),
    ("اللغه العربيه", "اللغة العربية"),
    ("اللغه الانجليزيه", "اللغة الإنجليزية"),
    ("الانجليزي", "اللغة الإنجليزية"),
    ("انجليزي", "اللغة الإنجليزية"),
    ("الانجليزيه", "اللغة الإنجليزية"),
    ("الاجتماعيات", "الاجتماعيات"),
    ("اجتماعيات", "الاجتماعيات"),
    ("التاريخ", "التاريخ"),
    ("الجغرافيا", "الجغرافيا"),
    ("التربيه الاسلاميه", "التربية الإسلامية"),
    ("الفقه", "الفقه"),
    ("الحديث", "الحديث"),
    ("التوحيد", "التوحيد"),
    ("التفسير", "التفسير"),
    ("التربيه الفنيه", "التربية الفنية"),
    ("الحاسب", "الحاسب"),
    ("الحاسوب", "الحاسب"),
    ("المهارات الرقميه", "المهارات الرقمية"),
)


# Arabic ordinal grade names → canonical "الصف …".
_GRADE_NAMES: tuple[tuple[str, str], ...] = (
    ("الصف الاول", "الصف الأول"),
    ("الصف الثاني", "الصف الثاني"),
    ("الصف الثالث", "الصف الثالث"),
    ("الصف الرابع", "الصف الرابع"),
    ("الصف الخامس", "الصف الخامس"),
    ("الصف السادس", "الصف السادس"),
    ("الصف السابع", "الصف السابع"),
    ("الصف الثامن", "الصف الثامن"),
    ("الصف التاسع", "الصف التاسع"),
    ("الصف العاشر", "الصف العاشر"),
    ("الصف الحادي عشر", "الصف الحادي عشر"),
    ("الصف الثاني عشر", "الصف الثاني عشر"),
    ("اول ابتدائي", "الصف الأول"),
    ("ثاني ابتدائي", "الصف الثاني"),
    ("ثالث ابتدائي", "الصف الثالث"),
    ("رابع ابتدائي", "الصف الرابع"),
    ("خامس ابتدائي", "الصف الخامس"),
    ("سادس ابتدائي", "الصف السادس"),
    ("اول متوسط", "الصف الأول المتوسط"),
    ("ثاني متوسط", "الصف الثاني المتوسط"),
    ("ثالث متوسط", "الصف الثالث المتوسط"),
    ("اول ثانوي", "الصف الأول الثانوي"),
    ("ثاني ثانوي", "الصف الثاني الثانوي"),
    ("ثالث ثانوي", "الصف الثالث الثانوي"),
)

# Bare-ordinal table — used only when we already know we're inside a
# "for-grade" context (after "للصف" / "صف"). Match through a regex so
# that ordinals appearing elsewhere ("الفصل الدراسي الثاني") don't
# leak in.
_BARE_ORDINAL_TO_GRADE: dict[str, str] = {
    "الاول": "الصف الأول",
    "الثاني": "الصف الثاني",
    "الثالث": "الصف الثالث",
    "الرابع": "الصف الرابع",
    "الخامس": "الصف الخامس",
    "السادس": "الصف السادس",
    "السابع": "الصف السابع",
    "الثامن": "الصف الثامن",
    "التاسع": "الصف التاسع",
    "العاشر": "الصف العاشر",
    "الحادي عشر": "الصف الحادي عشر",
    "الثاني عشر": "الصف الثاني عشر",
}

# "للصف الرابع" / "صف الرابع" / "في الصف الرابع" → grade.
_FOR_GRADE_RE = re.compile(
    r"(?:ل?للصف|للصف|الصف|صف)\s+"
    r"(?P<g>الاول|الثاني\s+عشر|الحادي\s+عشر|الثاني|الثالث|الرابع|الخامس|"
    r"السادس|السابع|الثامن|التاسع|العاشر)"
)


# Stage triggers.
_STAGE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("الابتدائيه", "المرحلة الابتدائية"),
    ("ابتدائي", "المرحلة الابتدائية"),
    ("ابتدائيه", "المرحلة الابتدائية"),
    ("المتوسطه", "المرحلة المتوسطة"),
    ("متوسط", "المرحلة المتوسطة"),
    ("متوسطه", "المرحلة المتوسطة"),
    ("الثانويه", "المرحلة الثانوية"),
    ("ثانوي", "المرحلة الثانوية"),
    ("ثانويه", "المرحلة الثانوية"),
)


_SEMESTER_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("الفصل الدراسي الاول", "الفصل الدراسي الأول"),
    ("الفصل الاول", "الفصل الدراسي الأول"),
    ("الفصل الدراسي الثاني", "الفصل الدراسي الثاني"),
    ("الفصل الثاني", "الفصل الدراسي الثاني"),
    ("الفصل الدراسي الثالث", "الفصل الدراسي الثالث"),
    ("الفصل الثالث", "الفصل الدراسي الثالث"),
)


_EXAM_TYPE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("اختبار نهائي", EXAM_TYPE_FINAL),
    ("نهائي", EXAM_TYPE_FINAL),
    ("اختبار قصير", EXAM_TYPE_QUICK),
    ("قصير", EXAM_TYPE_QUICK),
    ("اختبار شهري", EXAM_TYPE_MONTHLY),
    ("شهري", EXAM_TYPE_MONTHLY),
    ("اختبار عملي", EXAM_TYPE_PRACTICAL),
    ("عملي", EXAM_TYPE_PRACTICAL),
    ("ورقه قياس", EXAM_TYPE_QIYAS),
    ("قياس", EXAM_TYPE_QIYAS),
    ("واجب", EXAM_TYPE_HOMEWORK),
)


# Unit / lesson / week regex (operate on normalised text).
_UNIT_RE = re.compile(
    r"الوحده\s+(?P<u>الاولي|الثانيه|الثالثه|الرابعه|الخامسه|السادسه|\d{1,2})"
)
_LESSON_RE = re.compile(r"درس\s+(?P<l>[^\.\n،,]+)")
_WEEK_RE = re.compile(r"الاسبوع\s+(?P<w>\d{1,2})")


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────


def parse_exam_slots(text: str | None) -> ExamSlots:
    """Pull every slot we can from ``text``. Missing slots stay None."""
    if not text:
        return ExamSlots()

    norm = normalize(text)
    if not norm:
        return ExamSlots()

    subject = _first_match(_SUBJECT_BY_NORM_KEYWORD, norm)

    grade = _first_match(_GRADE_NAMES, norm)
    if not grade:
        # Bare ordinal must be preceded by a grade trigger — that
        # rules out "الفصل الدراسي الثاني" leaking through.
        m = _FOR_GRADE_RE.search(norm)
        if m:
            ordinal = re.sub(r"\s+", " ", m.group("g")).strip()
            grade = _BARE_ORDINAL_TO_GRADE.get(ordinal)

    stage = _first_match(_STAGE_KEYWORDS, norm)
    semester = _first_match(_SEMESTER_KEYWORDS, norm)
    exam_type = _first_match(_EXAM_TYPE_KEYWORDS, norm)

    unit = None
    m = _UNIT_RE.search(norm)
    if m:
        unit = f"الوحدة {m.group('u')}"

    lesson = None
    m = _LESSON_RE.search(norm)
    if m:
        lesson = (m.group("l") or "").strip()
        if len(lesson) > 60:
            lesson = lesson[:60]

    week = None
    m = _WEEK_RE.search(norm)
    if m:
        try:
            week = int(m.group("w"))
        except (ValueError, TypeError):
            week = None

    return ExamSlots(
        subject=subject, grade=grade, stage=stage, semester=semester,
        exam_type=exam_type, unit=unit, lesson=lesson, week=week,
    )


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _first_match(table: tuple[tuple[str, str], ...], norm: str) -> str | None:
    """Return the first canonical value whose normalised key appears in
    ``norm``. Sorts longer keys first so "الفصل الدراسي الأول" wins
    over "الفصل الأول"."""
    for key, value in sorted(table, key=lambda kv: -len(kv[0])):
        if key in norm:
            return value
    return None


__all__ = [
    "ExamSlots",
    "parse_exam_slots",
]
