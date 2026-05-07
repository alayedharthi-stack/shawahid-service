"""
Smart, multi-signal evidence classifier.
────────────────────────────────────────

Why this module exists
======================
The legacy classifier reasoned almost exclusively from the file
extension or filename, which produced two well-known regressions:

    • Every PDF defaulted to "ملفات إدارية" — even weekly plans
      and tests.
    • A photo of a worksheet was tagged as "نشاط صفي" because the
      filename was random.

Phase-3 rule: every classification decision must combine **at least
two** signals out of {filename, extracted_text, teacher_caption,
prior categories}. The legacy GPT path remains in place as the
ground truth — this module is a *cheap, deterministic pre-classifier*
the webhook calls before paying for an OpenAI round-trip.

The module is pure (no DB / network / GPT). It returns:

    ClassificationResult(
        category="التخطيط",
        confidence=0.83,
        importance="strong",
        reason="filename:خطة; text:نواتج التعلم",
    )

Tests live in ``tests/test_phase3_intelligence.py``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.intents import normalize


# ──────────────────────────────────────────────────────────────────────
# Public DTO
# ──────────────────────────────────────────────────────────────────────

# Officially supported categories — must match the names produced by
# `app.services.exporter._build_categories` so the export pipeline
# bins the evidence into the correct section.
CATEGORIES = (
    "التخطيط",
    "التقويم",
    "سجل المتابعة",
    "ملفات إدارية",
    "مصدر تعليمي",
    "التعلم النشط",
    "التعلم التعاوني",
    "التحفيز",
    "نشاط صفي",
    "الدورات والشهادات",
    "أخرى",
)

DEFAULT_CATEGORY = "ملفات إدارية"

IMPORTANCE_STRONG = "strong"
IMPORTANCE_MEDIUM = "medium"
IMPORTANCE_SIMPLE = "simple"  # plan-mandated label (== "weak" in older code)


@dataclass(frozen=True)
class ClassificationResult:
    category: str
    confidence: float
    importance: str
    reason: str
    needs_confirmation: bool = False


# ──────────────────────────────────────────────────────────────────────
# Keyword banks
# ──────────────────────────────────────────────────────────────────────

# Each tuple is (set of normalised keywords → category, weight).
# Heavier weights win on ties. All keywords are pre-normalised
# (no diacritics, hamza/yaa/taa unified) — see ``intents.normalize``.
_KEYWORD_RULES: tuple[tuple[tuple[str, ...], str, int], ...] = (
    # ── Strong PLANNING signals ────────────────────────────────────────
    (("نواتج التعلم", "نواتج تعلم", "توزيع المنهج", "توزيع منهج",
      "خطه فصليه", "خطه اسبوعيه", "خطه يوميه", "تحضير درس",
      "الاسبوع الاول", "الاسبوع الثاني", "الوحده الاولي",
      "التوزيع الزمني"),
     "التخطيط", 4),
    (("خطه", "خطط", "توزيع", "تحضير", "اسبوع", "درس", "منهج",
      "lesson plan", "curriculum"),
     "التخطيط", 2),

    # ── Strong ASSESSMENT signals ──────────────────────────────────────
    (("اختبار نهائي", "ورقه عمل", "اسئله الاختبار", "كشف الدرجات",
      "كشف درجات", "مهمه ادائيه", "رصد الدرجات", "توزيع الدرجات"),
     "التقويم", 4),
    (("اختبار", "تقويم", "درجات", "ورقه", "نتائج", "تقرير اداء",
      "exam", "test", "quiz", "worksheet"),
     "التقويم", 2),

    # ── Strong FOLLOW-UP signals ───────────────────────────────────────
    (("سجل المتابعه", "سجل متابعه", "كشف الحضور", "كشف حضور",
      "كشف الغياب", "متابعه يوميه", "حضور وغياب", "متابعه الطلاب"),
     "سجل المتابعة", 4),
    (("حضور", "غياب", "متابعه", "سجل", "مشاركه الطلاب"),
     "سجل المتابعة", 2),

    # ── ADMINISTRATIVE signals (timetable belongs here, not planning) ──
    (("جدول الحصص", "جدول مدرسي", "تعميم رقم", "قرار رقم",
      "خطاب رسمي", "نحيطكم علما", "المديريه العامه",
      "توقيع المدير", "توقيع المديره"),
     "ملفات إدارية", 4),
    (("تعميم", "خطاب", "قرار", "اجتماع", "دوام", "اداري",
      "circular", "memo"),
     "ملفات إدارية", 2),

    # ── Resources / enrichment ─────────────────────────────────────────
    (("رابط اثرائي", "ماده اثرائيه", "فيديو تعليمي", "شرح الدرس",
      "مصدر تعليمي"),
     "مصدر تعليمي", 4),
    (("اثراء", "اثرائي", "شرح", "موقع", "video", "youtube"),
     "مصدر تعليمي", 2),

    # ── Active learning / cooperative learning ─────────────────────────
    (("تعلم تعاوني", "العمل الجماعي", "مجموعات الطلاب",
      "تعلم نشط", "تفاعل الطلاب", "تعلم بالممارسه"),
     "التعلم النشط", 3),
    (("نشاط", "تفاعل", "مجموعات", "activity"),
     "نشاط صفي", 2),

    # ── Certificates ────────────────────────────────────────────────────
    (("شهاده اتمام", "شهاده تقدير", "اجتاز بنجاح", "يشهد بان",
      "certificate"),
     "الدورات والشهادات", 3),
    (("شهاده", "دوره", "تدريب", "ورشه"),
     "الدورات والشهادات", 2),
)

# ── Heuristic structural signals ──────────────────────────────────────

_TIMETABLE_DAYS = ("الاحد", "الاثنين", "الثلاثاء", "الاربعاء", "الخميس")
_TIMETABLE_PERIODS = (
    "الحصه الاولي", "الحصه الثانيه", "الحصه الثالثه",
    "الحصه الرابعه", "الحصه الخامسه", "الحصه السادسه",
)


def _is_school_timetable(norm_text: str) -> bool:
    """A school timetable is a (days × periods) grid. We require ≥2
    distinct days AND ≥1 distinct period to avoid false positives on
    long lesson plans that happen to mention "الاثنين" once."""
    day_hits = sum(1 for d in _TIMETABLE_DAYS if d in norm_text)
    period_hits = sum(1 for p in _TIMETABLE_PERIODS if p in norm_text)
    return day_hits >= 2 and period_hits >= 1


# ──────────────────────────────────────────────────────────────────────
# Classifier
# ──────────────────────────────────────────────────────────────────────

def classify_evidence(
    *,
    filename: str | None = None,
    extracted_text: str | None = None,
    caption: str | None = None,
    evidence_type: str | None = None,
    prior_categories: list[str] | None = None,
) -> ClassificationResult:
    """Heuristic, deterministic evidence classifier.

    Combines four signals:

        1. ``filename``           — extension / keywords in the filename
        2. ``extracted_text``     — OCR / PDF text / message body
        3. ``caption``            — what the teacher wrote alongside
        4. ``prior_categories``   — last few categories the teacher
                                    used (acts as a soft tie-breaker)

    Each rule contributes a weight to the candidate category. The
    highest-scoring candidate wins. Ties fall back to
    ``DEFAULT_CATEGORY`` and ``needs_confirmation=True``.
    """
    norm_filename = normalize(filename or "")
    norm_text = normalize(extracted_text or "")
    norm_caption = normalize(caption or "")
    blob = " ".join(filter(None, [norm_filename, norm_text, norm_caption]))

    scores: dict[str, int] = {}
    reasons: list[str] = []

    # ── 1. Hard override: school timetable in the body ─────────────────
    if _is_school_timetable(norm_text):
        return ClassificationResult(
            category="ملفات إدارية",
            confidence=0.92,
            importance=IMPORTANCE_MEDIUM,
            reason="structural:جدول مدرسي (أيام × حصص)",
            needs_confirmation=False,
        )

    # ── 2. Keyword-bank scoring ────────────────────────────────────────
    for keywords, category, weight in _KEYWORD_RULES:
        hits = [kw for kw in keywords if kw in blob]
        if not hits:
            continue
        scores[category] = scores.get(category, 0) + weight * len(hits)
        reasons.append(f"{category}:{hits[0]}")

    # ── 3. Soft tie-breaker: previous categories the teacher used ──────
    if prior_categories:
        for prev in prior_categories[-5:]:
            if prev in scores:
                scores[prev] = scores.get(prev, 0) + 1

    # ── 4. Decide ──────────────────────────────────────────────────────
    if not scores:
        return ClassificationResult(
            category=DEFAULT_CATEGORY,
            confidence=0.35,
            importance=IMPORTANCE_SIMPLE,
            reason="no signals — defaulted",
            needs_confirmation=True,
        )

    best_cat, best_score = max(scores.items(), key=lambda kv: kv[1])
    total = sum(scores.values()) or 1
    raw_confidence = best_score / total

    # Normalise into 0.5-0.95 so the webhook can compare across
    # signals without absorbing the raw integer.
    confidence = round(0.5 + 0.45 * raw_confidence, 2)

    importance = score_importance(
        category=best_cat,
        evidence_type=evidence_type,
        norm_blob=blob,
        confidence=confidence,
    )

    needs_confirmation = confidence < 0.65 or len(scores) >= 3
    return ClassificationResult(
        category=best_cat,
        confidence=confidence,
        importance=importance,
        reason="; ".join(reasons[:3]),
        needs_confirmation=needs_confirmation,
    )


# ──────────────────────────────────────────────────────────────────────
# Importance scorer
# ──────────────────────────────────────────────────────────────────────

# Rich-content categories — even a single image in these is worth a
# medium card. Administrative paperwork starts at "simple".
_HIGH_VALUE_CATEGORIES = frozenset({
    "التخطيط",
    "التعلم النشط",
    "التعلم التعاوني",
    "التحفيز",
    "نشاط صفي",
})

_LOW_VALUE_CATEGORIES = frozenset({
    "ملفات إدارية",
    "أخرى",
})


def score_importance(
    *,
    category: str,
    evidence_type: str | None = None,
    norm_blob: str = "",
    confidence: float = 0.0,
) -> str:
    """Map a (category, evidence_type, signals, confidence) tuple to one
    of ``IMPORTANCE_STRONG / IMPORTANCE_MEDIUM / IMPORTANCE_SIMPLE``.

    Heuristics (kept transparent on purpose so QA can audit them):

        • Strong: high-value categories with rich text or video,
          OR confidence ≥ 0.85.
        • Simple: low-value categories with no extracted content,
          OR confidence < 0.55.
        • Medium: everything else.
    """
    et = (evidence_type or "").lower()

    # Long, descriptive text in a high-value category → strong card.
    rich_text = len(norm_blob) >= 200
    has_media = et in {"image", "video", "audio", "voice", "image_gallery"}

    if category in _HIGH_VALUE_CATEGORIES and (rich_text or et == "video"):
        return IMPORTANCE_STRONG
    if confidence >= 0.85 and category not in _LOW_VALUE_CATEGORIES:
        return IMPORTANCE_STRONG

    if category in _LOW_VALUE_CATEGORIES and not rich_text:
        return IMPORTANCE_SIMPLE
    if confidence < 0.55 and not has_media:
        return IMPORTANCE_SIMPLE

    return IMPORTANCE_MEDIUM
