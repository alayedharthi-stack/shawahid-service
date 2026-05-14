"""
exam_rewrite.text_cleaner — strip boilerplate, signatures, names, …

The cleaner is intentionally conservative: we'd rather leave a noisy
line in the exam than silently drop a real question. Removed line
patterns are well-known boilerplate that no Saudi exam template
relies on for instructional content.

Phase-3 contract:
    • Pure function: no DB, no GPT, no network.
    • Stable, deterministic output.
    • Whitespace is normalised but Arabic letters are NOT folded —
      the downstream renderer needs to display the original spelling.
"""
from __future__ import annotations

import re
import unicodedata


# Lines we always drop (case-insensitive, after light normalization).
_DROP_LINE_RE = re.compile(
    r"^("
    # Page numbers
    r"صفحه\s*\d+(\s*(/|من)\s*\d+)?"
    r"|page\s*\d+(\s*(/|of)\s*\d+)?"
    # Teacher / student signature & name lines (drop only the prefix
    # line — the field stays in the rewritten exam but the old
    # teacher's literal name does not).
    r"|اسم\s*المعلم(ه|ة)?\s*[:：].*"
    r"|اسم\s*الطالب(ه|ة)?\s*[:：].*"
    r"|توقيع\s*المعلم(ه|ة)?\s*[:：].*"
    r"|توقيع\s*المدير(ه|ة)?\s*[:：].*"
    r"|توقيع\s*ولي\s*الام?ر?\s*[:：].*"
    r"|اعداد\s*المعلم(ه|ة)?\s*[:：].*"
    r"|اعتماد\s*المدير(ه|ة)?\s*[:：].*"
    # Standalone "أ. فلان" / "المعلم: ..." at the bottom of pages.
    # NOTE: the regex is applied to folded text, where أ→ا and ة→ه.
    r"|ا\.\s*[\u0600-\u06FF\s]{3,30}$"
    r"|المعلم(ه)?\s*[:：]\s*[\u0600-\u06FF\s]{3,40}$"
    r"|المدير(ه)?\s*[:：]\s*[\u0600-\u06FF\s]{3,40}$"
    # Phone / WhatsApp footers
    r"|[\+\d][\d\s\-]{7,}\s*$"
    r"|whatsapp\s*[:：]?.*"
    r"|واتس(اب)?\s*[:：]?.*"
    # Emails
    r"|\S+@\S+\.\S+\s*$"
    # Empty bullets / decorative lines
    r"|[\u2500-\u259F\-_=\.\*~]{3,}\s*$"
    r"|\s*"
    r")$",
    flags=re.IGNORECASE,
)


# Inline scrubbing — patterns we strip from any line we keep.
_PHONE_INLINE_RE = re.compile(r"(?<!\d)\+?\d[\d\s\-]{6,}\d(?!\d)")
_EMAIL_INLINE_RE = re.compile(r"\S+@\S+\.\S+")
_PAGE_FOOTER_INLINE_RE = re.compile(r"\bصفحه\s*\d+(\s*(/|من)\s*\d+)?\b", flags=re.IGNORECASE)


# Whitespace normalisation
_MULTISPACE_RE = re.compile(r"[ \t\u00A0]{2,}")
_TRAILING_DOTS_RE = re.compile(r"[.…•·]{3,}")


def _normalize_for_drop_match(line: str) -> str:
    """Mirror the folding the drop regex was tuned against.

    We *only* use this folded form for the drop-line test — the
    returned/kept text is the original (whitespace-normalised) line.
    """
    folded = unicodedata.normalize("NFKC", line)
    folded = folded.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    folded = folded.replace("ى", "ي").replace("ئ", "ي").replace("ؤ", "و")
    folded = folded.replace("ة", "ه")
    folded = re.sub(r"\s+", " ", folded).strip().lower()
    return folded


def clean_lines(text: str) -> list[str]:
    """Return cleaned, ordered lines from ``text``.

    Boilerplate lines are dropped entirely. Kept lines have phone
    numbers / emails scrubbed and whitespace tidied.
    """
    if not text:
        return []
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        folded = _normalize_for_drop_match(line)
        if _DROP_LINE_RE.match(folded):
            continue
        # Scrub inline noise but keep the line.
        scrubbed = _PHONE_INLINE_RE.sub(" ", line)
        scrubbed = _EMAIL_INLINE_RE.sub(" ", scrubbed)
        scrubbed = _PAGE_FOOTER_INLINE_RE.sub(" ", scrubbed)
        scrubbed = _TRAILING_DOTS_RE.sub(" ", scrubbed)
        scrubbed = _MULTISPACE_RE.sub(" ", scrubbed).strip()
        if not scrubbed:
            continue
        out.append(scrubbed)
    return out


def clean_text(text: str) -> str:
    """Convenience: clean and rejoin with single newlines."""
    return "\n".join(clean_lines(text))


__all__ = ["clean_lines", "clean_text"]
