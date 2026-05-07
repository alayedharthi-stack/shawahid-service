"""
conversation_engine.profile_context — extract teacher profile updates.

The webhook today learns the teacher's name/subject/school only when
GPT happens to fill the ``profile_update`` payload. That works most of
the time but misses very common Arabic phrasings:

    • "أنا إياد محمد الحارثي"
    • "اسمي إياد"
    • "أدرس رياضيات"
    • "معلم متوسط"
    • "مدرستي ابتدائية الفيصل"

This module is a deterministic pre-extractor that runs *before* GPT.
It returns a ``ProfileUpdate`` describing what fields the text claims
to set, with a confidence score the caller can use to decide whether
to apply directly or stage as a pending confirmation.

Pure module: no DB, no GPT. Tests live in ``tests/test_conversation_engine.py``.
"""
from __future__ import annotations

import re

from app.conversation_engine.schemas import ProfileUpdate
from app.services.intents import normalize


# ──────────────────────────────────────────────────────────────────────
# Subject vocabulary
# ──────────────────────────────────────────────────────────────────────

# Maps normalised mention → canonical subject name (display form).
_SUBJECT_MAP: dict[str, str] = {
    "رياضيات": "الرياضيات",
    "الرياضيات": "الرياضيات",
    "علوم": "العلوم",
    "العلوم": "العلوم",
    "فيزياء": "الفيزياء",
    "كيمياء": "الكيمياء",
    "احياء": "الأحياء",
    "لغه عربيه": "اللغة العربية",
    "اللغه العربيه": "اللغة العربية",
    "العربيه": "اللغة العربية",
    "لغه انجليزيه": "اللغة الإنجليزية",
    "الانجليزيه": "اللغة الإنجليزية",
    "اجتماعيات": "الاجتماعيات",
    "الاجتماعيات": "الاجتماعيات",
    "تاريخ": "التاريخ",
    "جغرافيا": "الجغرافيا",
    "تربيه اسلاميه": "التربية الإسلامية",
    "اسلاميه": "التربية الإسلامية",
    "فقه": "الفقه",
    "توحيد": "التوحيد",
    "تجويد": "التجويد",
    "قران": "القرآن الكريم",
    "حاسب": "الحاسب",
    "حاسوب": "الحاسب",
    "فنيه": "التربية الفنية",
    "بدنيه": "التربية البدنية",
    "اسريه": "التربية الأسرية",
    "مهارات حياتيه": "المهارات الحياتية",
}

# Phrases that typically PRECEDE a subject mention.
_SUBJECT_TRIGGERS = (
    "ادرس", "ادرّس", "اعلم", "اعلّم", "تخصصي", "مادتي", "ماده",
    "معلم", "معلمه", "استاذ", "استاذه", "مدرس", "مدرسه",
)

# Education stages → canonical grade buckets.
_STAGE_MAP: dict[str, str] = {
    "ابتدائيه": "المرحلة الابتدائية",
    "ابتدائي": "المرحلة الابتدائية",
    "متوسطه": "المرحلة المتوسطة",
    "متوسط": "المرحلة المتوسطة",
    "ثانويه": "المرحلة الثانوية",
    "ثانوي": "المرحلة الثانوية",
    "روضه": "رياض الأطفال",
    "رياض اطفال": "رياض الأطفال",
}

# Specific grade numbers ("الصف الرابع").
_GRADE_NUMBERS = (
    ("الاول", "الصف الأول"),
    ("الثاني", "الصف الثاني"),
    ("الثالث", "الصف الثالث"),
    ("الرابع", "الصف الرابع"),
    ("الخامس", "الصف الخامس"),
    ("السادس", "الصف السادس"),
)


# ──────────────────────────────────────────────────────────────────────
# Extractors
# ──────────────────────────────────────────────────────────────────────

# Capture a person name following an introduction phrase. We greedily
# allow up to 5 Arabic name tokens (first/middle/last/lineage).
_NAME_INTRO_RE = re.compile(
    r"(?:انا|اسمي|اسمى|اسم المعلم(?:\s+الصحيح)?|هذا اسمي)\s+"
    r"((?:[\u0621-\u064A]+\s*){1,5})"
)

# Capture school name following "مدرستي" / "مدرسه" / "في مدرسه".
_SCHOOL_RE = re.compile(
    r"(?:مدرستي|في مدرسه|في مدرسة|مدرسه|مدرسة)\s+"
    r"((?:[\u0621-\u064A]+\s*){1,4})"
)

# Capture region: "منطقة الرياض", "في الرياض", "تعليم الرياض".
_REGION_RE = re.compile(
    r"(?:منطقه|منطقة|تعليم|في)\s+"
    r"(الرياض|جده|جدة|مكه|مكة|الدمام|المدينه|المدينة|تبوك|الاحساء|"
    r"عسير|نجران|حائل|القصيم|الباحه|الباحة|جيزان|الجوف|الشرقيه|الشرقية|الحدود الشماليه)"
)


def extract_profile_update(text: str | None) -> ProfileUpdate:
    """Best-effort profile-update extraction.

    Returns ``ProfileUpdate(fields={}, confidence=0.0)`` when nothing
    is detected. Callers should treat low-confidence updates as
    candidates for confirmation, not direct writes.
    """
    if not text:
        return ProfileUpdate()

    norm = normalize(text)
    if not norm:
        return ProfileUpdate()

    fields: dict[str, str] = {}
    reasons: list[str] = []
    confidences: list[float] = []

    # ── Name ───────────────────────────────────────────────────────────
    name_match = _NAME_INTRO_RE.search(norm)
    if name_match:
        captured = _clean_name(name_match.group(1))
        if captured:
            # Canonicalize through the name dictionary so the stored
            # spelling carries the proper hamza/taa-marbuta forms even
            # though our matcher operated on the normalised text.
            from app.conversation_engine.name_intelligence import (
                normalize_full_name,
            )
            candidate = normalize_full_name(captured)
            canonical = candidate.normalized or captured
            fields["name"] = canonical
            reasons.append(f"name:{canonical}")
            trigger = name_match.group(0).split()[0]
            base_conf = 0.9 if trigger in {"اسمي", "اسمى"} else 0.7
            # Pull the confidence down a bit if the dictionary couldn't
            # vouch for the spelling — the planner uses this to decide
            # whether to ask for confirmation.
            confidences.append(min(base_conf, candidate.confidence + 0.05))

    # ── Subject ────────────────────────────────────────────────────────
    subject = _detect_subject(norm)
    if subject:
        fields["subject"] = subject
        reasons.append(f"subject:{subject}")
        confidences.append(0.85)

    # ── Grade / stage ──────────────────────────────────────────────────
    grade = _detect_grade(norm)
    if grade:
        fields["grade"] = grade
        reasons.append(f"grade:{grade}")
        confidences.append(0.8)

    # ── School ─────────────────────────────────────────────────────────
    school_match = _SCHOOL_RE.search(norm)
    if school_match:
        school = _clean_name(school_match.group(1))
        if school and len(school) >= 3:
            fields["school"] = school
            reasons.append(f"school:{school}")
            confidences.append(0.7)

    # ── Region ─────────────────────────────────────────────────────────
    region_match = _REGION_RE.search(norm)
    if region_match:
        region = region_match.group(1)
        fields["region"] = region
        reasons.append(f"region:{region}")
        confidences.append(0.75)

    if not fields:
        return ProfileUpdate()

    confidence = round(min(confidences) if confidences else 0.0, 2)
    return ProfileUpdate(
        fields=fields,
        confidence=confidence,
        reason="; ".join(reasons),
    )


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _clean_name(raw: str) -> str:
    """Trim trailing connector words a regex greedily captured."""
    # Drop common trailing words that are NOT part of a name.
    words = raw.strip().split()
    stopwords = {
        "وادرس", "ادرس", "ادرّس", "اعلم", "اعلّم", "في", "من",
        "تخصصي", "مادتي", "ماده", "وانا", "كذا", "هذا", "هذه",
        "وفي", "ومدرستي", "مدرستي", "بمدرسة", "بمدرسه", "ومنطقتي",
    }
    cleaned: list[str] = []
    for w in words:
        if w in stopwords:
            break
        cleaned.append(w)
    return " ".join(cleaned).strip()


def _detect_subject(norm: str) -> str | None:
    """Pick the longest subject mention; require a trigger nearby OR
    one of the canonical multi-word forms (which are unambiguous on
    their own)."""
    best: tuple[str, int] | None = None  # (canonical, length)
    for mention, canonical in _SUBJECT_MAP.items():
        if mention not in norm:
            continue
        # Multi-word mentions imply they really refer to the subject.
        is_unambiguous = " " in mention or len(mention) >= 6
        # Single-word like "علوم" needs a trigger like "ادرس" nearby.
        if not is_unambiguous and not _has_trigger_near(norm, mention):
            continue
        if best is None or len(mention) > best[1]:
            best = (canonical, len(mention))
    return best[0] if best else None


def _has_trigger_near(norm: str, mention: str) -> bool:
    """``mention`` appears within ~25 chars of a teaching trigger word."""
    idx = norm.find(mention)
    if idx < 0:
        return False
    window = norm[max(0, idx - 25): idx + len(mention) + 25]
    return any(t in window for t in _SUBJECT_TRIGGERS)


def _detect_grade(norm: str) -> str | None:
    """Detect either an education stage or a specific grade number."""
    for mention, canonical in _STAGE_MAP.items():
        if mention in norm:
            return canonical

    # Match "الصف <ordinal>" or just "<ordinal>" preceded by معلم/مدرس.
    for ordinal, canonical in _GRADE_NUMBERS:
        if f"الصف {ordinal}" in norm:
            return canonical
        # "معلم الرابع" / "مدرس السادس"
        for tr in ("معلم", "مدرس", "استاذ"):
            if f"{tr} {ordinal}" in norm:
                return canonical
    return None


__all__ = ["extract_profile_update"]
