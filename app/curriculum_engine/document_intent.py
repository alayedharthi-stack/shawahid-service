"""
curriculum_engine.document_intent — structural document intent.

Why this module exists
======================
The deterministic classifier in ``app/services/classification.py``
counts keyword hits. That works for short captions but loses on
documents that mix vocabulary, e.g. a *weekly plan* titled
"خطة تنفيذ أسبوعية" gets matched against both the planning bank
("خطة", "أسبوع") and the active-learning bank ("تنفيذ", "نشاط").

This module fixes that by switching the question from
"which keywords appear?" to **"what is the document trying to be?"**:

    • A planning document has *headings* like نواتج التعلم / التهيئة
      / الواجب and is structured around weeks/units/lessons.
    • An in-class activity report has *narrative* about what students
      did, with photos / observations.
    • An assessment has scores, questions, model answers.

The detector returns a ``DocumentIntent`` with explicit ``signals``
explaining the decision (auditable for QA).

Pure module. No DB / GPT / network.
"""
from __future__ import annotations

import re

from app.curriculum_engine.saudi_curriculum import (
    ADMIN_HEADINGS,
    ASSESSMENT_HEADINGS,
    CERTIFICATE_HEADINGS,
    FOLLOWUP_HEADINGS,
    PLANNING_HEADINGS,
    TIMETABLE_HEADINGS,
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
    DocumentIntent,
)
from app.services.intents import normalize


# ──────────────────────────────────────────────────────────────────────
# Heading detection — favours STRUCTURE over single keywords.
# A heading "scores" when it appears in a way that looks like a section
# title (start of line, surrounded by whitespace, or followed by a colon).
# ──────────────────────────────────────────────────────────────────────


_HEADING_LINE_RE = re.compile(
    r"(?:^|\n)\s*([\u0600-\u06FF\s]{2,60}?)\s*[:：]?\s*(?=\n|$)",
    re.MULTILINE,
)


def _count_heading_hits(norm_text: str, headings: tuple[str, ...]) -> tuple[int, list[str]]:
    """Count how many of ``headings`` appear *as section headings*.

    A "heading hit" is stricter than a substring match — the heading
    must appear at the start of a line OR be followed by a colon.
    Falls back to a substring match (with a smaller weight applied
    elsewhere) when the first pass finds nothing.
    """
    hits: list[str] = []
    for heading in headings:
        # Strict pattern: start-of-string OR after whitespace AND
        # followed by colon / newline / end-of-string.
        pattern = rf"(?:(?<=^)|(?<=\s)){re.escape(heading)}(?=\s*[:：\n]|\s|$)"
        if re.search(pattern, norm_text):
            hits.append(heading)
    return len(hits), hits


def _count_substring_hits(norm_text: str, headings: tuple[str, ...]) -> tuple[int, list[str]]:
    hits = [h for h in headings if h in norm_text]
    return len(hits), hits


# ──────────────────────────────────────────────────────────────────────
# Structural cues unique to planning vs in-class
# ──────────────────────────────────────────────────────────────────────

# Planning-specific cues: a list of weeks, numbered objectives, "الوحدة الأولى"
_PLANNING_CUES = (
    "الاسبوع الاول", "الاسبوع الثاني", "الاسبوع الثالث", "الاسبوع الرابع",
    "الوحده الاولي", "الوحده الثانيه",
    "زمن الحصه", "عدد الحصص",
    "استراتيجيات التدريس",
    "نواتج التعلم", "اهداف الدرس",
    "التوزيع الزمني", "توزيع المنهج",
)

# In-class report cues: narrative tense + present-action language.
_IN_CLASS_CUES = (
    "اليوم قمت", "قام الطلاب", "نفذ الطلاب", "شارك الطلاب",
    "تفاعل الطلاب", "قمنا بتنفيذ", "قام الطالب", "نشاط طلابي",
    "في الحصه قام", "خلال الحصه", "اثناء الدرس",
    "قام الطلاب باعداد", "صور النشاط", "صور تنفيذ",
)

# School timetable: requires multiple days × periods.
_TIMETABLE_DAYS = ("الاحد", "الاثنين", "الثلاثاء", "الاربعاء", "الخميس")
_TIMETABLE_PERIODS = ("الحصه الاولي", "الحصه الثانيه", "الحصه الثالثه")


def _is_school_timetable(norm: str) -> bool:
    days = sum(1 for d in _TIMETABLE_DAYS if d in norm)
    periods = sum(1 for p in _TIMETABLE_PERIODS if p in norm)
    return days >= 2 and periods >= 1


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────


def detect_document_intent(
    *,
    text: str | None = None,
    title: str | None = None,
    filename: str | None = None,
) -> DocumentIntent:
    """Classify a document by its structural intent.

    All three inputs are optional. When ``text`` is empty the detector
    falls back to title/filename keyword matching with low confidence.
    """
    norm_text = normalize(text or "")
    norm_title = normalize(title or "")
    norm_filename = normalize(filename or "")
    blob = " ".join(filter(None, [norm_title, norm_filename, norm_text]))

    if not blob:
        return DocumentIntent(
            intent=DOC_INTENT_UNKNOWN,
            confidence=0.0,
            signals=(),
            reason="no inputs",
        )

    signals: list[str] = []
    scores: dict[str, float] = {}

    # ── Strong structural overrides ────────────────────────────────────
    if _is_school_timetable(norm_text):
        return DocumentIntent(
            intent=DOC_INTENT_TIMETABLE,
            confidence=0.92,
            signals=("structure:days×periods",),
            reason="جدول مدرسي (≥2 أيام × حصة)",
        )

    # Certificate detection is unambiguous when phrased explicitly.
    cert_strict, cert_hits = _count_heading_hits(blob, CERTIFICATE_HEADINGS)
    if cert_strict >= 1:
        return DocumentIntent(
            intent=DOC_INTENT_CERTIFICATE,
            confidence=0.9,
            signals=tuple(f"heading:{h}" for h in cert_hits[:3]),
            reason="عنوان شهادة",
        )

    # ── Heading scoring (stronger weight, structural) ──────────────────
    for headings, intent_label, weight in (
        (PLANNING_HEADINGS, DOC_INTENT_PLANNING, 3.0),
        (ASSESSMENT_HEADINGS, DOC_INTENT_ASSESSMENT, 3.0),
        (FOLLOWUP_HEADINGS, DOC_INTENT_FOLLOWUP, 3.0),
        (ADMIN_HEADINGS, DOC_INTENT_ADMIN, 3.0),
        (TIMETABLE_HEADINGS, DOC_INTENT_TIMETABLE, 3.0),
    ):
        n, hits = _count_heading_hits(blob, headings)
        if n:
            scores[intent_label] = scores.get(intent_label, 0.0) + weight * n
            signals.extend(f"heading:{h}" for h in hits[:2])

    # ── Substring fallback (lighter weight) ────────────────────────────
    for headings, intent_label, weight in (
        (PLANNING_HEADINGS, DOC_INTENT_PLANNING, 1.0),
        (ASSESSMENT_HEADINGS, DOC_INTENT_ASSESSMENT, 1.0),
        (FOLLOWUP_HEADINGS, DOC_INTENT_FOLLOWUP, 1.0),
        (ADMIN_HEADINGS, DOC_INTENT_ADMIN, 1.0),
    ):
        n, hits = _count_substring_hits(blob, headings)
        if n:
            scores[intent_label] = scores.get(intent_label, 0.0) + weight * n
            signals.extend(f"substring:{h}" for h in hits[:1])

    # ── Cue scoring: distinguishes planning vs in-class ────────────────
    planning_cues = sum(1 for cue in _PLANNING_CUES if cue in blob)
    in_class_cues = sum(1 for cue in _IN_CLASS_CUES if cue in blob)

    if planning_cues:
        scores[DOC_INTENT_PLANNING] = scores.get(DOC_INTENT_PLANNING, 0.0) + 2.0 * planning_cues
        signals.append(f"planning_cues:{planning_cues}")
    if in_class_cues:
        scores[DOC_INTENT_IN_CLASS] = scores.get(DOC_INTENT_IN_CLASS, 0.0) + 2.0 * in_class_cues
        signals.append(f"in_class_cues:{in_class_cues}")

    # ── Critical heuristic: if the document has BOTH "تنفيذ" and the
    #     planning skeleton (نواتج / تهيئة / واجب / أسبوع), it is a
    #     PLAN of execution, not an in-class report. This is exactly
    #     the "خطة تنفيذ أسبوعية" case the AI eval surfaced.
    has_planning_skeleton = (
        "نواتج التعلم" in blob
        or "اهداف الدرس" in blob
        or "التهيئه" in blob
        or "الواجب" in blob
        or "توزيع المنهج" in blob
        or "التوزيع الزمني" in blob
    )
    has_week_marker = any(
        marker in blob
        for marker in ("الاسبوع الاول", "الاسبوع الثاني", "الاسبوع الثالث",
                       "خطه اسبوعيه", "خطه فصليه")
    )
    if has_planning_skeleton and ("تنفيذ" in blob or "تنفيذيه" in blob or "اسبوعيه" in blob):
        scores[DOC_INTENT_PLANNING] = scores.get(DOC_INTENT_PLANNING, 0.0) + 4.0
        scores[DOC_INTENT_IN_CLASS] = max(0.0, scores.get(DOC_INTENT_IN_CLASS, 0.0) - 2.0)
        signals.append("override:planning_skeleton+execution_word")

    if has_week_marker and "تنفيذ" in blob:
        scores[DOC_INTENT_PLANNING] = scores.get(DOC_INTENT_PLANNING, 0.0) + 2.0
        signals.append("override:week_marker+execution_word")

    # ── Decide ─────────────────────────────────────────────────────────
    if not scores:
        return DocumentIntent(
            intent=DOC_INTENT_UNKNOWN,
            confidence=0.3,
            signals=tuple(signals[:5]),
            reason="no structural signal",
        )

    best_intent, best_score = max(scores.items(), key=lambda kv: kv[1])
    total = sum(scores.values()) or 1.0
    raw = best_score / total
    confidence = round(0.5 + 0.45 * raw, 2)

    # Confidence boost when there are multiple structural signals.
    if best_score >= 6.0:
        confidence = min(0.95, confidence + 0.05)

    return DocumentIntent(
        intent=best_intent,
        confidence=confidence,
        signals=tuple(signals[:6]),
        reason=f"best={best_intent} score={best_score:.1f} of {total:.1f}",
    )


__all__ = ["detect_document_intent"]
