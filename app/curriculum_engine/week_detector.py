"""
curriculum_engine.week_detector — extract the week number from text.

Detects phrases like:

    "الأسبوع الأول"        → 1
    "الأسبوع الثاني عشر"   → 12
    "الأسبوع 5"            → 5
    "أسبوع رقم 7"          → 7

Returns ``None`` when no clear week marker is present. Pure module.
"""
from __future__ import annotations

import re

from app.services.intents import normalize


# Arabic ordinals 1-15. Higher weeks are uncommon in a single semester.
_ORDINALS: dict[str, int] = {
    "الاول": 1, "الاولي": 1,
    "الثاني": 2, "الثانيه": 2,
    "الثالث": 3, "الثالثه": 3,
    "الرابع": 4, "الرابعه": 4,
    "الخامس": 5, "الخامسه": 5,
    "السادس": 6, "السادسه": 6,
    "السابع": 7, "السابعه": 7,
    "الثامن": 8, "الثامنه": 8,
    "التاسع": 9, "التاسعه": 9,
    "العاشر": 10, "العاشره": 10,
    "الحادي عشر": 11, "الحاديه عشره": 11,
    "الثاني عشر": 12, "الثانيه عشره": 12,
    "الثالث عشر": 13, "الثالثه عشره": 13,
    "الرابع عشر": 14,
    "الخامس عشر": 15,
}


_NUMERIC_WEEK_RE = re.compile(r"(?:الاسبوع|اسبوع)\s*(?:رقم\s*)?(\d{1,2})")


def detect_week(text: str | None) -> int | None:
    """Return the detected week number or ``None``."""
    if not text:
        return None
    norm = normalize(text)
    if not norm:
        return None

    # Numeric form first ("الأسبوع 5" / "أسبوع رقم 7").
    m = _NUMERIC_WEEK_RE.search(norm)
    if m:
        try:
            n = int(m.group(1))
            if 1 <= n <= 30:
                return n
        except ValueError:
            pass

    # Ordinal form, longest first so "الثاني عشر" wins over "الثاني".
    for ordinal, value in sorted(_ORDINALS.items(), key=lambda kv: -len(kv[0])):
        if f"الاسبوع {ordinal}" in norm or f"اسبوع {ordinal}" in norm:
            return value

    return None


__all__ = ["detect_week"]
