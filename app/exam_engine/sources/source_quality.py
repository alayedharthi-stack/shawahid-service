"""
exam_engine.sources.source_quality — sample quality control.

Phase-11 contract
=================
Every sample passes through ``check_sample_quality`` before it can
become an exam. The check is multi-faceted:

    • clarity            — questions readable, not garbled
    • duplicates         — same question doesn't appear twice
    • OCR corruption     — random Latin chars / mojibake
    • count sanity       — at least one usable question, ≤ N
    • subject / grade    — declared metadata matches the query

Pure module. Returns ``QualityReport`` (defined in ``base.py``) so
callers can branch without exception handling.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.exam_engine.sources.base import QualityReport
from app.exam_engine.sources.source_normalizer import NormalizedSample


# Maximum reasonable question count per single source paper.
MAX_QUESTIONS_PER_SAMPLE = 60
MIN_QUESTIONS_PER_SAMPLE = 1


# OCR-corruption signature. We flag a sample as corrupted when any
# question text contains:
#   • a long run of Latin letters (unexpected in Arabic exam papers)
#   • mojibake characters (U+FFFD, lone surrogates)
#   • an unusual symbol density
_LATIN_RUN_RE = re.compile(r"[A-Za-z]{6,}")
_MOJIBAKE_RE = re.compile(r"[\uFFFD\uFFFE\uFFFF]")
_SYMBOL_DENSITY_RE = re.compile(r"[#@\$%\^\*\~`<>{}\\|]{4,}")


@dataclass
class QualityFlags:
    """Granular flags surfaced inside the ``QualityReport``."""

    has_duplicates: bool = False
    has_ocr_corruption: bool = False
    has_garbled_text: bool = False
    too_few_questions: bool = False
    too_many_questions: bool = False
    subject_mismatch: bool = False
    grade_mismatch: bool = False
    semester_mismatch: bool = False

    def as_tuple(self) -> tuple[str, ...]:
        return tuple(name for name, val in self.__dict__.items() if val)


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────


def check_sample_quality(
    sample: NormalizedSample,
    *,
    expected_subject: str | None = None,
    expected_grade: str | None = None,
    expected_stage: str | None = None,
    expected_semester: str | None = None,
) -> QualityReport:
    """Run every quality gate and return a single report."""
    flags = QualityFlags()
    reasons: list[str] = []

    # ── Count sanity ──────────────────────────────────────────────────
    n = sample.question_count
    if n < MIN_QUESTIONS_PER_SAMPLE:
        flags.too_few_questions = True
        reasons.append("لا توجد أسئلة قابلة للاستخدام")
    if n > MAX_QUESTIONS_PER_SAMPLE:
        flags.too_many_questions = True
        reasons.append(f"عدد الأسئلة غير منطقي ({n})")

    # ── Per-question checks ───────────────────────────────────────────
    seen_normalized: set[str] = set()
    for q in sample.questions:
        if _is_garbled(q.text):
            flags.has_garbled_text = True
            reasons.append("نص سؤال غير واضح")
            break
        if _looks_ocr_corrupted(q.text):
            flags.has_ocr_corruption = True
            reasons.append("OCR corruption مكتشف")
            break

        norm = _normalise_for_dup(q.text)
        if norm and norm in seen_normalized:
            flags.has_duplicates = True
            # don't break — caller may want to know how many dups
        seen_normalized.add(norm)

    # ── Metadata gates ────────────────────────────────────────────────
    meta = sample.meta or {}
    if expected_subject and meta.get("subject"):
        if not _arabic_substring_match(expected_subject, str(meta["subject"])):
            flags.subject_mismatch = True
            reasons.append("المادة لا تطابق الطلب")
    if expected_grade and meta.get("grade"):
        if not _arabic_substring_match(expected_grade, str(meta["grade"])):
            flags.grade_mismatch = True
            reasons.append("الصف لا يطابق الطلب")
    if expected_stage and meta.get("stage"):
        if not _arabic_substring_match(expected_stage, str(meta["stage"])):
            flags.grade_mismatch = True
            reasons.append("المرحلة لا تطابق الطلب")
    if expected_semester and meta.get("semester"):
        if not _arabic_substring_match(expected_semester, str(meta["semester"])):
            flags.semester_mismatch = True
            reasons.append("الفصل الدراسي لا يطابق الطلب")

    # ── Decide ────────────────────────────────────────────────────────
    blocking = (
        flags.too_few_questions
        or flags.has_ocr_corruption
        or flags.has_garbled_text
        or flags.subject_mismatch
        or flags.grade_mismatch
        or flags.semester_mismatch
    )
    is_acceptable = not blocking
    return QualityReport(
        is_acceptable=is_acceptable,
        reason="؛ ".join(reasons) if reasons else "ok",
        flags=flags.as_tuple(),
    )


# ──────────────────────────────────────────────────────────────────────
# Internals
# ──────────────────────────────────────────────────────────────────────


def _looks_ocr_corrupted(text: str) -> bool:
    if not text:
        return False
    if _MOJIBAKE_RE.search(text):
        return True
    if _LATIN_RUN_RE.search(text):
        return True
    if _SYMBOL_DENSITY_RE.search(text):
        return True
    return False


def _is_garbled(text: str) -> bool:
    """Reject obviously broken text — mostly digits, tiny, or single char."""
    if not text:
        return True
    stripped = text.strip()
    if len(stripped) < 4:
        return True
    # Mostly digits / symbols → garbled.
    arabic = sum(1 for ch in stripped if "\u0600" <= ch <= "\u06FF")
    if arabic == 0 and not _LATIN_RUN_RE.search(stripped):
        # No Arabic AND no proper Latin run → likely corrupted.
        return True
    return False


def _normalise_for_dup(text: str) -> str:
    if not text:
        return ""
    cleaned = " ".join(text.split())
    # Drop diacritics + tatweel for dedup.
    for ch in "\u064B\u064C\u064D\u064E\u064F\u0650\u0651\u0652\u0640":
        cleaned = cleaned.replace(ch, "")
    # Unify hamza variants / yaa / taa marbuta.
    cleaned = cleaned.translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه"}))
    return cleaned.lower()


def _arabic_substring_match(expected: str, actual: str) -> bool:
    """Loose equality: ignores diacritics, hamza, and word order."""
    e = _normalise_for_dup(expected)
    a = _normalise_for_dup(actual)
    if not e or not a:
        return True  # missing metadata → don't block
    return (e in a) or (a in e)


__all__ = [
    "QualityFlags",
    "check_sample_quality",
    "MAX_QUESTIONS_PER_SAMPLE",
    "MIN_QUESTIONS_PER_SAMPLE",
]
