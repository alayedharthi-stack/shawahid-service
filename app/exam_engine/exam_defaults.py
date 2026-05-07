"""
exam_engine.exam_defaults — smart defaults for question count / marks / time.

Phase-12 contract
=================
The teacher rarely volunteers numerical defaults. We compute sensible
ones from (stage, exam_type) so the bot can move forward instead of
asking three more questions.

The matrix is intentionally small and conservative — the teacher can
always override later via slot extraction or by replying with a custom
duration.

Pure module. No DB / GPT / network.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.exam_engine.schemas import (
    EXAM_TYPE_FINAL,
    EXAM_TYPE_HOMEWORK,
    EXAM_TYPE_MONTHLY,
    EXAM_TYPE_PRACTICAL,
    EXAM_TYPE_QIYAS,
    EXAM_TYPE_QUICK,
)


@dataclass(frozen=True)
class ExamDefaults:
    total_questions: int
    total_marks: int
    duration_minutes: int


# Stage-aware defaults. Keys are the canonical Arabic stage labels plus
# their normalised (no-diacritic) aliases the slot parser produces.
_PRIMARY_LABELS = {"المرحلة الابتدائية", "primary", "ابتدائي"}
_INTERMEDIATE_LABELS = {"المرحلة المتوسطة", "intermediate", "متوسط"}
_SECONDARY_LABELS = {"المرحلة الثانوية", "secondary", "ثانوي"}


# (stage_bucket, exam_type) → defaults
_TABLE: dict[tuple[str, str], ExamDefaults] = {
    # ── Quick exams ────────────────────────────────────────────────
    ("primary", EXAM_TYPE_QUICK):       ExamDefaults(5,  10, 15),
    ("intermediate", EXAM_TYPE_QUICK):  ExamDefaults(8,  15, 20),
    ("secondary", EXAM_TYPE_QUICK):     ExamDefaults(10, 20, 25),

    # ── Monthly exams ─────────────────────────────────────────────
    ("primary", EXAM_TYPE_MONTHLY):       ExamDefaults(10, 20, 30),
    ("intermediate", EXAM_TYPE_MONTHLY):  ExamDefaults(12, 25, 40),
    ("secondary", EXAM_TYPE_MONTHLY):     ExamDefaults(15, 30, 45),

    # ── Final exams ───────────────────────────────────────────────
    ("primary", EXAM_TYPE_FINAL):       ExamDefaults(15, 30, 60),
    ("intermediate", EXAM_TYPE_FINAL):  ExamDefaults(20, 40, 90),
    ("secondary", EXAM_TYPE_FINAL):     ExamDefaults(25, 50, 120),

    # ── Practical / Qiyas / Homework ──────────────────────────────
    ("primary", EXAM_TYPE_PRACTICAL):       ExamDefaults(5, 10, 30),
    ("intermediate", EXAM_TYPE_PRACTICAL):  ExamDefaults(6, 15, 45),
    ("secondary", EXAM_TYPE_PRACTICAL):     ExamDefaults(8, 20, 60),

    ("primary", EXAM_TYPE_QIYAS):       ExamDefaults(15, 30, 60),
    ("intermediate", EXAM_TYPE_QIYAS):  ExamDefaults(20, 40, 90),
    ("secondary", EXAM_TYPE_QIYAS):     ExamDefaults(25, 50, 120),

    ("primary", EXAM_TYPE_HOMEWORK):       ExamDefaults(3, 5, 15),
    ("intermediate", EXAM_TYPE_HOMEWORK):  ExamDefaults(4, 8, 20),
    ("secondary", EXAM_TYPE_HOMEWORK):     ExamDefaults(5, 10, 30),
}


# Last-resort fallback when stage is unknown.
_FALLBACK_BY_TYPE: dict[str, ExamDefaults] = {
    EXAM_TYPE_QUICK:     ExamDefaults(8, 15, 20),
    EXAM_TYPE_MONTHLY:   ExamDefaults(12, 25, 40),
    EXAM_TYPE_FINAL:     ExamDefaults(20, 40, 90),
    EXAM_TYPE_PRACTICAL: ExamDefaults(6, 15, 45),
    EXAM_TYPE_QIYAS:     ExamDefaults(20, 40, 90),
    EXAM_TYPE_HOMEWORK:  ExamDefaults(4, 8, 20),
}


def smart_defaults(
    *,
    stage: str | None,
    exam_type: str | None,
) -> ExamDefaults:
    """Return a sensible defaults bundle for ``(stage, exam_type)``."""
    bucket = _stage_bucket(stage)
    if bucket and exam_type:
        out = _TABLE.get((bucket, exam_type))
        if out:
            return out
    if exam_type:
        return _FALLBACK_BY_TYPE.get(exam_type, _FALLBACK_BY_TYPE[EXAM_TYPE_QUICK])
    return _FALLBACK_BY_TYPE[EXAM_TYPE_QUICK]


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _stage_bucket(stage: str | None) -> str | None:
    if not stage:
        return None
    s = stage.strip()
    if s in _PRIMARY_LABELS:
        return "primary"
    if s in _INTERMEDIATE_LABELS:
        return "intermediate"
    if s in _SECONDARY_LABELS:
        return "secondary"
    # Substring fallback — covers messy strings like "الصف الرابع الابتدائي".
    if "ابتدائي" in s:
        return "primary"
    if "متوسط" in s:
        return "intermediate"
    if "ثانوي" in s:
        return "secondary"
    return None


__all__ = [
    "ExamDefaults",
    "smart_defaults",
]
