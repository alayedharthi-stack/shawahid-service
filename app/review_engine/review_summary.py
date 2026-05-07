"""
review_summary — build a short, smart Arabic summary of a ReviewSession.

The output is plain text suitable for a WhatsApp message or a review
page banner. It is intentionally concise — three to six lines — so it
fits a mobile screen at a glance.

Hard rules:
    • No DB, no ORM, no templates, no Playwright, no export_engine.
    • Input is always a ReviewSession DTO.
"""
from __future__ import annotations

from app.review_engine.schemas import ReviewSession


def build_summary_text(session: ReviewSession) -> str:
    """Return a multi-line Arabic summary for the teacher.

    Example output
    --------------
    لديك 42 شاهدًا جاهزًا ✅
    📌 8 تحتاج مراجعة بسيطة
    ⚠️ 2 مكررة
    ⭐ 12 شاهدًا قويًا
    """
    lines: list[str] = []

    active = session.active_items
    if active == 0:
        lines.append("لا يوجد شواهد جاهزة بعد 📭")
        return "\n".join(lines)

    lines.append(f"لديك {active} شاهدًا جاهزًا ✅")

    if session.low_confidence_count:
        lines.append(f"📌 {session.low_confidence_count} تحتاج مراجعة بسيطة")

    if session.duplicates_count:
        lines.append(f"⚠️ {session.duplicates_count} مكررة")

    if session.strong_count:
        lines.append(f"⭐ {session.strong_count} شاهدًا قويًا")

    excluded = session.total_items - session.active_items
    if excluded:
        lines.append(f"🗑️ {excluded} مُستبعدة من التصدير")

    return "\n".join(lines)


def build_categories_line(session: ReviewSession) -> str:
    """Return a compact one-line summary of the top categories.

    Example: "التخطيط (8) • التقويم (5) • التنفيذ داخل الصف (4)"
    """
    cats = session.categories_summary
    if not cats:
        return ""
    parts = [f"{cat} ({count})" for cat, count in list(cats.items())[:5]]
    return " • ".join(parts)


def build_export_readiness(session: ReviewSession) -> str:
    """Return a one-line export-readiness assessment.

    Used as a sub-line in WhatsApp messages:
        "الملف جاهز للتصدير" / "راجع الشواهد قبل التصدير"
    """
    if session.low_confidence_count == 0 and session.duplicates_count == 0:
        return "الملف جاهز للتصدير 🚀"
    hints: list[str] = []
    if session.low_confidence_count:
        hints.append(f"{session.low_confidence_count} شاهد يحتاج مراجعة")
    if session.duplicates_count:
        hints.append(f"{session.duplicates_count} مكرر")
    return "راجع الشواهد قبل التصدير ✏️ — " + " و ".join(hints)
