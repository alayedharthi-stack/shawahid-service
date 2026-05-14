"""
exam_rewrite.metadata_extractor — pull (subject, grade, type, …) from text.

All detectors are pure, deterministic functions. They operate on the
cleaned text produced by ``text_cleaner.clean_text`` and never reach
out to GPT / DB / network.

Conservative bias: returning ``None`` is preferred over guessing the
wrong value. The orchestrator records a ``warning`` when a critical
field is missing.
"""
from __future__ import annotations

import re
import unicodedata

from app.exam_rewrite.schemas import (
    EXAM_TYPE_ASSESSMENT,
    EXAM_TYPE_ASSIGNMENT,
    EXAM_TYPE_EXAM,
    EXAM_TYPE_UNKNOWN,
    EXAM_TYPE_WORKSHEET,
)


# ──────────────────────────────────────────────────────────────────────
# Light folding for matching (keeps text untouched for output)
# ──────────────────────────────────────────────────────────────────────


def _fold(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[\u064B-\u065F\u0670]", "", text)  # diacritics
    text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    text = text.replace("ى", "ي").replace("ئ", "ي").replace("ؤ", "و")
    text = text.replace("ة", "ه")
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


# ──────────────────────────────────────────────────────────────────────
# Subject detection
# ──────────────────────────────────────────────────────────────────────


# Map of folded-keyword → canonical Arabic subject name.
_SUBJECT_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("الرياضيات",         "الرياضيات"),
    ("رياضيات",           "الرياضيات"),
    ("اللغه العربيه",     "اللغة العربية"),
    ("اللغه العربية",     "اللغة العربية"),
    ("اللغة العربيه",     "اللغة العربية"),
    ("اللغة العربية",     "اللغة العربية"),
    ("لغتي",              "لغتي"),
    ("اللغه الانجليزيه",  "اللغة الإنجليزية"),
    ("اللغه الانجليزية",  "اللغة الإنجليزية"),
    ("الانجليزي",         "اللغة الإنجليزية"),
    ("english",           "اللغة الإنجليزية"),
    ("العلوم",            "العلوم"),
    ("علوم",              "العلوم"),
    ("الفيزياء",          "الفيزياء"),
    ("الكيمياء",          "الكيمياء"),
    ("الاحياء",           "الأحياء"),
    ("الجغرافيا",         "الجغرافيا"),
    ("التاريخ",           "التاريخ"),
    ("الاجتماعيات",       "الدراسات الاجتماعية"),
    ("الدراسات الاجتماعيه","الدراسات الاجتماعية"),
    ("التربيه الاسلاميه", "التربية الإسلامية"),
    ("التربية الإسلامية", "التربية الإسلامية"),
    ("الدراسات الاسلاميه","الدراسات الإسلامية"),
    ("القران الكريم",     "القرآن الكريم"),
    ("التوحيد",           "التوحيد"),
    ("الفقه",             "الفقه"),
    ("الحديث",            "الحديث"),
    ("التفسير",           "التفسير"),
    ("الحاسب",            "الحاسب الآلي"),
    ("الحاسوب",           "الحاسب الآلي"),
    ("التربيه الفنيه",    "التربية الفنية"),
    ("التربيه البدنيه",   "التربية البدنية"),
    ("المهارات الرقميه",  "المهارات الرقمية"),
    ("المهارات الحياتيه", "المهارات الحياتية"),
)


def detect_subject(text: str | None) -> str | None:
    """Return the canonical subject name or ``None``."""
    if not text:
        return None
    folded = _fold(text)
    if not folded:
        return None

    # 1) Explicit label form: "المادة: الرياضيات".
    m = re.search(r"الماده\s*[:：]\s*([\u0600-\u06FF\s]{3,40})", folded)
    if m:
        label = m.group(1).strip()
        for kw, canonical in _SUBJECT_KEYWORDS:
            if kw in label:
                return canonical

    # 2) Free-form keyword scan — first hit wins.
    for kw, canonical in _SUBJECT_KEYWORDS:
        if kw in folded:
            return canonical
    return None


# ──────────────────────────────────────────────────────────────────────
# Grade detection
# ──────────────────────────────────────────────────────────────────────


_ORDINAL_TO_DIGIT: dict[str, int] = {
    "الاول": 1, "الاولي": 1, "الأول": 1, "اول": 1,
    "الثاني": 2, "الثانيه": 2, "ثاني": 2,
    "الثالث": 3, "الثالثه": 3, "ثالث": 3,
    "الرابع": 4, "الرابعه": 4, "رابع": 4,
    "الخامس": 5, "الخامسه": 5, "خامس": 5,
    "السادس": 6, "السادسه": 6, "سادس": 6,
    "السابع": 7, "السابعه": 7, "سابع": 7,
    "الثامن": 8, "الثامنه": 8, "ثامن": 8,
    "التاسع": 9, "التاسعه": 9, "تاسع": 9,
    "العاشر": 10, "العاشره": 10, "عاشر": 10,
    "الحادي عشر": 11, "الحادية عشره": 11,
    "الثاني عشر": 12, "الثانية عشره": 12,
}


_STAGE_KEYWORDS: dict[str, str] = {
    "ابتدائي": "ابتدائي",
    "الابتدائي": "ابتدائي",
    "ابتدائيه": "ابتدائي",
    "الابتدائيه": "ابتدائي",
    "متوسط": "متوسط",
    "المتوسط": "متوسط",
    "متوسطه": "متوسط",
    "المتوسطه": "متوسط",
    "ثانوي": "ثانوي",
    "الثانوي": "ثانوي",
    "ثانويه": "ثانوي",
    "الثانويه": "ثانوي",
}


_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


_STAGE_RE_GROUP = (
    r"(الابتدائي|الابتدائيه|ابتدائي|"
    r"المتوسط|المتوسطه|متوسط|"
    r"الثانوي|الثانويه|ثانوي)"
)


def detect_grade(text: str | None) -> str | None:
    """Return a human-readable grade like ``"الصف الخامس الابتدائي"``.

    Anchoring rules — both protect against false positives like
    "الفترة الأولى":
        1. ``الصف N``  (Arabic digit) — prefix required.
        2. ``الصف الخامس``  (ordinal after الصف/للصف) — prefix required.
        3. ``الخامس ابتدائي``  (ordinal before a stage word) — stage
           is required to anchor.
    """
    if not text:
        return None
    folded = _fold(text).translate(_AR_DIGITS)
    if not folded:
        return None

    # 1) "الصف 5" (+ optional stage).
    m = re.search(
        rf"(?:الصف|للصف)\s+(\d{{1,2}})\b\s*{_STAGE_RE_GROUP}?",
        folded,
    )
    if m:
        try:
            n = int(m.group(1))
        except ValueError:
            n = -1
        if 1 <= n <= 12:
            return _format_grade(n, m.group(2))

    # 2) "الصف الخامس" (+ optional stage). Prefix REQUIRED.
    ordinals_long_first = sorted(
        _ORDINAL_TO_DIGIT.keys(), key=len, reverse=True,
    )
    for ordinal in ordinals_long_first:
        m = re.search(
            rf"(?:الصف|للصف)\s+{re.escape(ordinal)}(?:\b|(?=\s|[.,:\-]|$))"
            rf"\s*{_STAGE_RE_GROUP}?",
            folded,
        )
        if m:
            return _format_grade(_ORDINAL_TO_DIGIT[ordinal], m.group(1))

    # 3) "الخامس ابتدائي" — ordinal+stage with no prefix. Stage
    # REQUIRED so we don't grab "الأولى" from "الفترة الأولى".
    for ordinal in ordinals_long_first:
        m = re.search(
            rf"\b{re.escape(ordinal)}\b\s+{_STAGE_RE_GROUP}",
            folded,
        )
        if m:
            return _format_grade(_ORDINAL_TO_DIGIT[ordinal], m.group(1))
    return None


def _format_grade(n: int, stage_raw: str | None) -> str:
    """Compose ``"الصف الخامس الابتدائي"`` style human strings."""
    ordinals_ar = {
        1: "الأول", 2: "الثاني", 3: "الثالث", 4: "الرابع",
        5: "الخامس", 6: "السادس", 7: "السابع", 8: "الثامن",
        9: "التاسع", 10: "العاشر", 11: "الحادي عشر",
        12: "الثاني عشر",
    }
    ordinal = ordinals_ar.get(n, f"{n}")
    stage = _STAGE_KEYWORDS.get(stage_raw) if stage_raw else None
    if stage:
        return f"الصف {ordinal} {stage}"
    # Stage missing — infer from grade number range.
    if 1 <= n <= 6:
        return f"الصف {ordinal} ابتدائي"
    if 7 <= n <= 9:
        return f"الصف {ordinal} متوسط"
    if 10 <= n <= 12:
        return f"الصف {ordinal} ثانوي"
    return f"الصف {ordinal}"


# ──────────────────────────────────────────────────────────────────────
# Exam type detection
# ──────────────────────────────────────────────────────────────────────


_EXAM_TYPE_RULES: tuple[tuple[str, str], ...] = (
    ("ورقه عمل",       EXAM_TYPE_WORKSHEET),
    ("ورقه العمل",     EXAM_TYPE_WORKSHEET),
    ("اوراق عمل",      EXAM_TYPE_WORKSHEET),
    ("نموذج قياس",     EXAM_TYPE_ASSESSMENT),
    ("قياس مهارات",    EXAM_TYPE_ASSESSMENT),
    ("اختبار قياس",    EXAM_TYPE_ASSESSMENT),
    ("التقويم",        EXAM_TYPE_ASSESSMENT),
    ("تقويم",          EXAM_TYPE_ASSESSMENT),
    ("اختبار",         EXAM_TYPE_EXAM),
    ("الاختبار",       EXAM_TYPE_EXAM),
    ("امتحان",         EXAM_TYPE_EXAM),
    ("الواجب",         EXAM_TYPE_ASSIGNMENT),
    ("واجب منزلي",     EXAM_TYPE_ASSIGNMENT),
    ("واجب",           EXAM_TYPE_ASSIGNMENT),
    ("تكليف",          EXAM_TYPE_ASSIGNMENT),
)


def detect_exam_type(text: str | None, hint: str | None = None) -> str:
    """Decide which of the four exam-like types this PDF is.

    ``hint`` is the classifier's ``detected_type`` (Phase 1) — when
    the text alone is ambiguous we trust the hint. Falls back to
    ``EXAM_TYPE_EXAM`` because by the time we reach this module the
    teacher has already chosen "rewrite this exam" — the worst case
    is still a sensible default.
    """
    if text:
        folded = _fold(text)
        for kw, kind in _EXAM_TYPE_RULES:
            if kw in folded:
                return kind
    if hint:
        h = hint.strip().lower()
        if h in {EXAM_TYPE_EXAM, EXAM_TYPE_WORKSHEET,
                 EXAM_TYPE_ASSIGNMENT, EXAM_TYPE_ASSESSMENT}:
            return h
    if text:
        return EXAM_TYPE_EXAM
    return EXAM_TYPE_UNKNOWN


# ──────────────────────────────────────────────────────────────────────
# Title / instructions / total-score detection
# ──────────────────────────────────────────────────────────────────────


_TITLE_HINT_RE = re.compile(
    r"(اختبار|الاختبار|امتحان|ورقه\s*عمل|واجب|الواجب|تقويم|نموذج\s*قياس)"
    r"[\u0600-\u06FF\s\-:،,]*",
    flags=re.IGNORECASE,
)


def detect_title(lines: list[str]) -> str | None:
    """Pick the most likely title from the first ~12 cleaned lines.

    Strategy: the first line that *starts* with a title-hint keyword
    and is reasonably short (< 80 chars) wins. If nothing matches we
    return the shortest non-trivial line in the top 6 — the page
    header is almost always there.
    """
    if not lines:
        return None
    head = lines[:12]
    folded_head = [_fold(ln) for ln in head]
    for original, folded in zip(head, folded_head):
        if 0 < len(original) < 80 and _TITLE_HINT_RE.match(folded):
            return original
    # Fallback: shortest substantial line in the top 6.
    candidates = [
        ln for ln in head[:6]
        if 6 <= len(ln) <= 80 and not ln.startswith("[صفحة")
    ]
    if candidates:
        return min(candidates, key=len)
    return None


_INSTRUCTION_PREFIXES_RE = re.compile(
    r"^("
    r"تعليمات\s*[:：]"
    r"|ملاحظه\s*[:：]"
    r"|ملاحظات\s*[:：]"
    r"|ارشادات\s*[:：]"
    r"|اقرا\s+ما\s+يلي"
    r"|اقرا\s+السوال"
    r")",
    flags=re.IGNORECASE,
)


def detect_instructions(lines: list[str]) -> str | None:
    """Return the first matching instructions line, if any."""
    if not lines:
        return None
    for ln in lines[:30]:
        folded = _fold(ln)
        if _INSTRUCTION_PREFIXES_RE.match(folded):
            # Strip the prefix and return the body — keep the
            # original casing/diacritics from ``ln``.
            return re.sub(r"^[^:：]+[:：]\s*", "", ln).strip() or ln
    return None


_TOTAL_SCORE_RES = (
    re.compile(r"(?:مجموع\s*الدرجات|الدرجه\s*الكليه|الدرجه\s*النهائيه)"
               r"\s*[:：]?\s*(\d{1,3}(?:\.\d{1,2})?)"),
    re.compile(r"(?:من|على)\s*(\d{1,3})\s*(?:درجه|درجات)"),
    re.compile(r"(\d{1,3})\s*(?:درجه|درجات)\s*$"),
)


def detect_total_score(text: str | None) -> float | None:
    """Best-effort total exam score. Returns ``None`` on no signal."""
    if not text:
        return None
    folded = _fold(text).translate(_AR_DIGITS)
    for rx in _TOTAL_SCORE_RES:
        m = rx.search(folded)
        if m:
            try:
                value = float(m.group(1))
            except ValueError:
                continue
            if 1 <= value <= 200:
                return value
    return None


__all__ = [
    "detect_subject",
    "detect_grade",
    "detect_exam_type",
    "detect_title",
    "detect_instructions",
    "detect_total_score",
]
