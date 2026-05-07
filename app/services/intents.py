"""
Semantic intent detection for inbound WhatsApp text.
─────────────────────────────────────────────────────

Phase-3 intelligence layer. Replaces brittle "exact-string match"
checks with normalised, multi-pattern recognition that survives:

    • Diacritics (الحَركات / تشكيل)
    • Hamza variants (أ إ آ ٱ → ا)
    • Tatweel and zero-width characters
    • Common typos / dialect substitutions
    • Word-order changes ("صدر الملف" vs "الملف صدر")

This module is *pure* — no DB, no network, no OpenAI. It exists so
the webhook can short-circuit obvious commands before paying the
GPT round-trip, and so unit tests stay deterministic.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


# ──────────────────────────────────────────────────────────────────────
# Recognised intents
# ──────────────────────────────────────────────────────────────────────

INTENT_EXPORT = "export"            # "صدر الآن", "أبغى الملف"
INTENT_REVIEW = "review"            # "راجع الشواهد", "أرني ملفي قبل التصدير"
INTENT_DELETE_LAST = "delete_last"  # "احذف آخر شاهد"
INTENT_DUPLICATE = "duplicate"      # "هذا مكرر"
INTENT_CATEGORY_HINT = "category"   # "هذه خطة" / "هذا اختبار"
INTENT_HELP = "help"                # "ساعدني / كيف"
INTENT_GREETING = "greeting"        # "السلام عليكم / مرحبا"
INTENT_NAME_CORRECTION = "name"     # voice/text correcting the saved name

# Phase-12: exam flow intents
INTENT_CREATE_EXAM = "create_exam"        # "أريد اختبار رياضيات"
INTENT_EXAM_MISSING_INFO = "exam_missing"  # follow-up giving missing slots
INTENT_EXAM_CONFIRM = "exam_confirm"       # "نعم اعتمده" after preview
INTENT_EXAM_REGENERATE = "exam_regen"      # "أعد إنشاءه"
INTENT_EXAM_EXPORT = "exam_export"         # "أرسل PDF" / "حمل الاختبار"

INTENT_NONE = "none"


# ──────────────────────────────────────────────────────────────────────
# Public DTO
# ──────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Intent:
    """A detected user intent.

    ``intent`` is one of the ``INTENT_*`` constants. ``confidence`` is
    a heuristic 0-1 score; higher values mean the webhook can act on
    the intent without asking GPT first. ``payload`` carries optional
    extracted data (e.g. the suggested category for category hints).
    """

    intent: str
    confidence: float = 0.0
    payload: dict | None = None


# ──────────────────────────────────────────────────────────────────────
# Arabic normalisation
# ──────────────────────────────────────────────────────────────────────

# All hamza-on-alef variants → bare alef.
_HAMZA_ALEF = re.compile(r"[أإآٱ]")
# Ya/alef-maqsura unification.
_YA = re.compile(r"[ىي]")
# Taa marbuta → haa.
_TAA = re.compile(r"ة")
# Tashkeel + tatweel + ZW chars.
_DIACRITICS = re.compile(r"[\u064B-\u065F\u0670\u0640\u200B-\u200F\u202A-\u202E]")


def normalize(text: str) -> str:
    """Aggressive Arabic normaliser used only for matching.

    NEVER apply to text that will be persisted — it loses information
    (e.g. distinction between ة and ه, ي and ى) which matters for
    teacher names.
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = _DIACRITICS.sub("", text)
    text = _HAMZA_ALEF.sub("ا", text)
    text = _YA.sub("ي", text)
    text = _TAA.sub("ه", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


# ──────────────────────────────────────────────────────────────────────
# Pattern banks
# ──────────────────────────────────────────────────────────────────────

# Keywords for each intent. Patterns are matched against the
# *normalised* form, so write them in normalised Arabic too.
_EXPORT_PATTERNS: tuple[str, ...] = (
    "صدر", "اصدر", "تصدير", "اصدار",
    "ابي الملف", "ابغي الملف", "اريد الملف",
    "ابغي ملف الشواهد", "ابي ملف الشواهد",
    "اريد ملف الشواهد", "اعطني الملف", "اعطيني الملف",
    "جهز ملفي", "جهز الملف", "جاهز للتصدير",
    "ارسل الملف", "ابعث الملف", "اخرج الملف",
    "حمل الملف", "طلع الملف", "اعطني الرابط",
)

_REVIEW_PATTERNS: tuple[str, ...] = (
    "راجع", "مراجعه", "اعرض", "ارني", "اعرض شواهدي",
    "اعرض الشواهد", "ارني ملفي", "ارني الشواهد",
    "ابي اراجع", "ابغي اراجع", "اريد المراجعه",
    "قبل التصدير", "اعرض الملف",
)

_DELETE_LAST_PATTERNS: tuple[str, ...] = (
    "احذف اخر شاهد", "احذف الشاهد الاخير", "احذف اخر ملف",
    "الغ اخر شاهد", "الغي اخر شاهد",
    "تراجع عن اخر", "ازل اخر شاهد",
)

_DUPLICATE_PATTERNS: tuple[str, ...] = (
    "هذا مكرر", "هذه مكرر", "ملف مكرر", "شاهد مكرر",
    "نفس الملف", "نفس الشاهد", "تكرار",
)

_HELP_PATTERNS: tuple[str, ...] = (
    "ساعدني", "كيف اعمل", "كيف استخدم", "ابغي مساعده",
    "اشرح لي", "وش اسوي", "كيف الطريقه",
)

_GREETING_PATTERNS: tuple[str, ...] = (
    "السلام عليكم", "مرحبا", "اهلا", "هلا", "صباح الخير",
    "مساء الخير", "حياك", "حياكم",
)


# ──────────────────────────────────────────────────────────────────────
# Phase-12: exam intents — patterns
# ──────────────────────────────────────────────────────────────────────

# Strong creation triggers ("create / make / want an exam"). When any of
# these matches, the webhook routes to the exam flow regardless of
# missing slots — the flow itself handles slot gathering.
_CREATE_EXAM_PATTERNS: tuple[str, ...] = (
    "اريد اختبار", "اريد اختبارا", "اريد اختبارًا",
    "ابغي اختبار", "ابغى اختبار", "ابي اختبار",
    "انشئ اختبار", "انشي اختبار", "انشئ لي اختبار", "انشي لي اختبار",
    "انشي لي نموذج", "انشئ لي نموذج", "اعطني اختبار", "اعطيني اختبار",
    "سو اختبار", "سو لي اختبار", "اعمل اختبار", "اعمل لي اختبار",
    "حضر اختبار", "حضر لي اختبار", "جهز اختبار", "جهز لي اختبار",
    "ابغي ورقه اختبار", "ابي ورقه اختبار", "اريد ورقه اختبار",
    "ابغي نموذج اختبار", "ابي نموذج اختبار", "اريد نموذج اختبار",
    "اختبار نهائي", "اختبار قصير", "اختبار شهري", "ورقه قياس",
)

# Confirmation / regenerate / send-PDF replies the teacher gives AFTER
# the bot generated a draft.
_EXAM_CONFIRM_PATTERNS: tuple[str, ...] = (
    "اعتمد الاختبار", "اعتمده", "نعم اعتمده", "موافق على الاختبار",
    "خلاص اعتمده", "تمام اعتمده", "وافق على الاختبار",
)
_EXAM_REGEN_PATTERNS: tuple[str, ...] = (
    "اعد الاختبار", "اعد انشاء الاختبار", "غير الاختبار",
    "بدل الاختبار", "ولد لي اختبار جديد", "اختبار اخر",
    "اعد التوليد", "نسخه ثانيه", "نسخه اخري",
)
_EXAM_SEND_PATTERNS: tuple[str, ...] = (
    "ارسل الاختبار", "ارسل لي الاختبار", "حمل الاختبار",
    "ارسل ال pdf", "ارسل البي دي اف", "ابعث الاختبار",
)


# ── Category hints — "هذا اختبار" / "هذه خطة" ──────────────────────────
# Maps a hint phrase → official category name (Arabic).
_CATEGORY_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    # Planning
    (("هذه خطه", "هذه خطه فصليه", "هذي خطه", "هذا توزيع منهج",
      "هذي خطه اسبوعيه", "هذا تحضير درس", "هذه خطه يوميه"),
     "التخطيط"),
    # Assessment
    (("هذا اختبار", "هذي ورقه عمل", "هذا تقويم", "هذا كشف درجات",
      "هذه ورقه عمل", "هذي درجات"),
     "التقويم"),
    # Follow-up
    (("هذا سجل متابعه", "هذي متابعه", "كشف حضور", "سجل غياب",
      "هذا حضور", "هذه متابعه طلاب"),
     "سجل المتابعة"),
    # Administrative
    (("هذا تعميم", "هذا خطاب", "هذا قرار", "هذه مراسله",
      "هذي اداريه", "هذا اداري"),
     "ملفات إدارية"),
    # Resources
    (("هذا رابط اثرائي", "هذا فيديو تعليمي", "هذي ماده اثرائيه",
      "هذا شرح", "هذي مصدر"),
     "مصدر تعليمي"),
    # Active learning
    (("هذا نشاط", "هذي مجموعات", "تعلم تعاوني", "تفاعل طلاب",
      "هذا تعلم نشط"),
     "التعلم النشط"),
)


# ──────────────────────────────────────────────────────────────────────
# Detector
# ──────────────────────────────────────────────────────────────────────

def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(p in text for p in patterns)


def detect_intent(text: str | None) -> Intent:
    """Classify a free-text message into a single intent.

    Returns ``Intent(INTENT_NONE, 0.0)`` when nothing matches —
    callers should treat that as "let GPT decide".
    """
    if not text:
        return Intent(INTENT_NONE, 0.0)
    norm = normalize(text)
    if not norm:
        return Intent(INTENT_NONE, 0.0)

    # Order matters: very specific intents first, generic ones last.
    if _matches_any(norm, _DELETE_LAST_PATTERNS):
        return Intent(INTENT_DELETE_LAST, 0.95)

    if _matches_any(norm, _DUPLICATE_PATTERNS):
        return Intent(INTENT_DUPLICATE, 0.9)

    # Phase-12: exam-creation intents must come BEFORE the category-hint
    # bank (which contains "هذا اختبار") and BEFORE export/review so a
    # teacher saying "أرسل الاختبار" gets routed to exam flow, not to
    # shawahid export.
    if _matches_any(norm, _EXAM_REGEN_PATTERNS):
        return Intent(INTENT_EXAM_REGENERATE, 0.85)
    if _matches_any(norm, _EXAM_CONFIRM_PATTERNS):
        return Intent(INTENT_EXAM_CONFIRM, 0.85)
    if _matches_any(norm, _EXAM_SEND_PATTERNS):
        return Intent(INTENT_EXAM_EXPORT, 0.85)
    if _matches_any(norm, _CREATE_EXAM_PATTERNS):
        return Intent(INTENT_CREATE_EXAM, 0.9)

    for hints, category in _CATEGORY_HINTS:
        if _matches_any(norm, hints):
            return Intent(INTENT_CATEGORY_HINT, 0.85, {"category": category})

    if _matches_any(norm, _REVIEW_PATTERNS):
        return Intent(INTENT_REVIEW, 0.85)

    if _matches_any(norm, _EXPORT_PATTERNS):
        return Intent(INTENT_EXPORT, 0.9)

    if _matches_any(norm, _HELP_PATTERNS):
        return Intent(INTENT_HELP, 0.7)

    if _matches_any(norm, _GREETING_PATTERNS):
        return Intent(INTENT_GREETING, 0.7)

    return Intent(INTENT_NONE, 0.0)


# ──────────────────────────────────────────────────────────────────────
# Name change detection (voice transcripts often differ from the
# saved name by 1-2 characters — we want to ALWAYS confirm before
# overwriting). Returns True when caller should ask the teacher.
# ──────────────────────────────────────────────────────────────────────

def looks_like_name_change(
    transcript: str | None,
    *,
    current_name: str | None,
) -> bool:
    """Heuristic: does the voice message contain a person's name that
    differs from the currently stored teacher name?

    Conservative on purpose — false positives are cheap (we just ask
    for confirmation), false negatives risk silently corrupting the
    teacher's official name.
    """
    if not transcript or not current_name:
        return False
    norm_t = normalize(transcript)
    norm_c = normalize(current_name)
    if not norm_t or not norm_c:
        return False

    # Trigger phrases that indicate the speaker is announcing a name.
    name_triggers = (
        "اسمي", "اسمى", "انا اسمي", "انا اسمى",
        "اعتمد", "اعتمدوا", "غير اسمي", "صحح اسمي",
        "اسم المعلم", "الاسم الصحيح",
    )
    if not any(t in norm_t for t in name_triggers):
        return False

    # Skip confirmation only when the transcript contains the
    # current name as a contiguous phrase. Inserting a new word
    # (e.g. middle name) is still a *change* that must be confirmed.
    if norm_c in norm_t:
        return False
    return True
