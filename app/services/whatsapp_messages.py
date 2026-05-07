"""
Phase-3 WhatsApp message builders.
──────────────────────────────────

This module is intentionally a *separate* file from ``whatsapp.py``
so the existing builders keep their behaviour unchanged. Phase-3
adds a small, opinionated set of replies that follow the new tone
guide:

    • Short — at most 4 short lines.
    • Calm  — never alarming, never apologetic when things go right.
    • One emoji per visual block — no emoji-spam.
    • Reuse the same emoji vocabulary so the teacher recognises it
      across every screen.

Emoji vocabulary (kept tiny on purpose):
    ✅  success / saved
    🔍  analysing
    ⭐  strong evidence
    ✏️  needs review
    📌  bullet (only when listing categories in a batch summary)
    🗂️  category
    🌟  exceptional praise (used sparingly)
    ⚠️  duplicate / blocking warning

This module is pure: no DB, no network, no GPT. Tests can call it
directly and assert on the exact string output.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.services.classification import (
    IMPORTANCE_MEDIUM,
    IMPORTANCE_SIMPLE,
    IMPORTANCE_STRONG,
)


# ──────────────────────────────────────────────────────────────────────
# Type label registry — used in confirmation messages.
# Mirrors the existing ``whatsapp._EV_TYPE_LABELS`` table but kept
# local so this module has zero coupling to the legacy builder.
# ──────────────────────────────────────────────────────────────────────

_TYPE_LABELS: dict[str, str] = {
    "pdf":      "ملف PDF",
    "document": "مستند",
    "image":    "صورة",
    "video":    "فيديو",
    "audio":    "تسجيل صوتي",
    "voice":    "رسالة صوتية",
    "url":      "رابط",
    "text":     "نص",
}


_IMPORTANCE_LABELS = {
    IMPORTANCE_STRONG: "قوي",
    IMPORTANCE_MEDIUM: "متوسط",
    IMPORTANCE_SIMPLE: "بسيط",
}


# ──────────────────────────────────────────────────────────────────────
# 1. File received (first ack — must arrive within ~1s)
# ──────────────────────────────────────────────────────────────────────

def build_file_received_message(ev_type: str | None = None) -> str:
    """Quick acknowledgement sent the moment a file lands.

    Two short lines, no extra detail — keeps the teacher confident
    the message was received without waiting for the full analysis.
    """
    type_label = _TYPE_LABELS.get((ev_type or "").lower(), "الملف")
    return (
        f"وصلني {type_label} ✅\n"
        "جارٍ تحليله الآن… 🔍"
    )


# ──────────────────────────────────────────────────────────────────────
# 2. Smart save confirmation (replaces the verbose legacy message)
# ──────────────────────────────────────────────────────────────────────

def build_evidence_saved_smart(
    *,
    ev_type: str,
    category: str,
    title: str | None = None,
    importance: str = IMPORTANCE_MEDIUM,
    needs_review: bool = False,
    is_duplicate: bool = False,
) -> str:
    """Final post-save reply for a single evidence.

    Layout (always max 5 short lines):

        تم حفظ الشاهد وتحليله بنجاح ✅
        📌 النوع: <type>
        🗂️ المحور: <category>
        ⭐ قوة الشاهد: <strong/medium/simple>      ← only when not 'simple'
        ✏️ يمكنك مراجعة المحور قبل التصدير      ← only if uncertain

    Duplicate files short-circuit to a single warning line.
    """
    if is_duplicate:
        return _build_duplicate_message(ev_type, category, title)

    type_label = _TYPE_LABELS.get((ev_type or "").lower(), "ملف")
    importance_label = _IMPORTANCE_LABELS.get(importance, "")

    lines: list[str] = ["تم حفظ الشاهد وتحليله بنجاح ✅"]
    if title:
        lines.append(f"📌 العنوان: {title}")
    lines.append(f"📌 النوع: {type_label}")
    if category:
        lines.append(f"🗂️ المحور: {category}")
    # Only mention importance when it adds signal — "simple" is the
    # default and showing it would feel like criticism of the
    # teacher's evidence.
    if importance != IMPORTANCE_SIMPLE and importance_label:
        lines.append(f"⭐ قوة الشاهد: {importance_label}")
    if needs_review:
        lines.append("✏️ حفظته في المحور الأقرب — يمكنك مراجعته قبل التصدير")
    return "\n".join(lines)


def _build_duplicate_message(
    ev_type: str, category: str, title: str | None
) -> str:
    type_label = _TYPE_LABELS.get((ev_type or "").lower(), "الملف")
    extra = []
    if title:
        extra.append(f"📌 {title}")
    if category:
        extra.append(f"🗂️ {category}")
    body = ("\n" + "\n".join(extra)) if extra else ""
    return f"⚠️ هذا {type_label} موجود مسبقًا في ملف الشواهد{body}"


# ──────────────────────────────────────────────────────────────────────
# 3. Strong-evidence praise (used at most once per save)
# ──────────────────────────────────────────────────────────────────────

def build_strong_evidence_callout() -> str:
    """One-line praise appended only for IMPORTANCE_STRONG saves."""
    return "هذا شاهد قوي ومناسب جدًا لملف الإنجاز 🌟"


# ──────────────────────────────────────────────────────────────────────
# 4. Uncertain-classification note
# ──────────────────────────────────────────────────────────────────────

def build_uncertain_classification_note() -> str:
    return "حفظته مؤقتًا في المحور الأقرب، ويمكنك مراجعته قبل التصدير ✏️"


# ──────────────────────────────────────────────────────────────────────
# 5. Name-change confirmation question
# ──────────────────────────────────────────────────────────────────────

def build_name_confirmation_question(suggested_name: str) -> str:
    """Always-confirm flow for any change to the official teacher name.

    The actual two interactive buttons (نعم / لا) are sent by the
    webhook — this module returns only the message body.
    """
    safe = (suggested_name or "").strip() or "—"
    return (
        "هل تقصد اعتماد الاسم التالي رسميًا؟\n"
        f"\u201c{safe}\u201d\n\n"
        "✅ نعم، اعتمده\n"
        "✏️ لا، سأكتبه من جديد"
    )


# ──────────────────────────────────────────────────────────────────────
# 6. Batch summary (multi-file arrival)
# ──────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class BatchItem:
    """One row in a multi-file ack message."""
    category: str
    needs_review: bool = False


def build_batch_summary(items: list[BatchItem]) -> str:
    """Single tidy reply when the teacher dumps several files in a row.

    Output (≤8 lines total):

        تم استلام 5 شواهد ✅
        📌 2 في التخطيط
        📌 1 في التقويم
        📌 1 في سجل المتابعة
        📌 1 يحتاج مراجعة بسيطة
    """
    if not items:
        return ""

    counts: dict[str, int] = {}
    review_total = 0
    for it in items:
        counts[it.category] = counts.get(it.category, 0) + 1
        if it.needs_review:
            review_total += 1

    total = len(items)
    lines: list[str] = [f"تم استلام {total} شواهد ✅"]
    # Sort by descending count, then by name for stable output.
    for cat, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"📌 {n} في {cat}")
    if review_total:
        lines.append(f"📌 {review_total} يحتاج مراجعة بسيطة")
    return "\n".join(lines)


# ── Phase-5: review_engine message builders ────────────────────────────


def build_review_ready_message(
    *,
    active_count: int,
    needs_review_count: int = 0,
    duplicates_count: int = 0,
    strong_count: int = 0,
) -> str:
    """Notify the teacher that their review page is ready.

    Called after the export-readiness check in the webhook but *before*
    sending the review link. Gives the teacher a concise overview so
    the link feels motivated.

    Example output:
        تم تجهيز صفحة مراجعة الشواهد ✅

        يمكنك الآن:
        ✏️ تعديل التصنيفات
        🗑️ حذف المكرر
        ⭐ مراجعة الشواهد القوية
    """
    lines: list[str] = [
        "تم تجهيز صفحة مراجعة الشواهد ✅",
        "",
        "يمكنك الآن:",
        "✏️ تعديل التصنيفات",
        "🗑️ حذف المكرر",
        "⭐ مراجعة الشواهد القوية",
    ]
    if active_count:
        lines.append(f"\n📊 لديك {active_count} شاهدًا جاهزًا")
    if strong_count:
        lines.append(f"⭐ {strong_count} شاهدًا قويًا")
    if needs_review_count:
        lines.append(f"✏️ {needs_review_count} تحتاج مراجعة")
    if duplicates_count:
        lines.append(f"⚠️ {duplicates_count} مكررة")
    return "\n".join(lines)


def build_review_link_message(review_url: str) -> str:
    """Return a short, clickable message with the review link.

    Example output:
        🔗 رابط المراجعة:
        https://...
    """
    return f"🔗 رابط المراجعة:\n{review_url}"
