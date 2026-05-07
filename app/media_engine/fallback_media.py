"""
Fallback cards.

Whenever a pipeline fails (corrupt file, unsupported MIME, missing
storage path, blocked CDN URL) we *do not* drop the evidence — that
violates the "never silently lose a teacher's work" rule. Instead
we return a canonical :class:`FallbackCard` describing what to show
in its place.

The renderer maps each card to a small visual block (icon + label)
and the WhatsApp builder maps it to a short user-friendly message.

Hard rules:
    • No ORM / DB / Playwright / network.
    • No HTML / CSS — this module returns *data*, not markup.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.media_engine.schemas import (
    FALLBACK_TYPE_AUDIO,
    FALLBACK_TYPE_FILE,
    FALLBACK_TYPE_IMAGE,
    FALLBACK_TYPE_PDF,
    FALLBACK_TYPE_URL,
    FALLBACK_TYPE_VIDEO,
    MEDIA_AUDIO,
    MEDIA_DOCUMENT,
    MEDIA_IMAGE,
    MEDIA_PDF,
    MEDIA_URL,
    MEDIA_VIDEO,
    MEDIA_VOICE,
)


@dataclass(frozen=True)
class FallbackCard:
    """A renderer-agnostic description of a fallback to show.

    Fields:
        ``fallback_type`` — one of the ``FALLBACK_TYPE_*`` constants.
        ``icon``          — single emoji used by both the PDF card
                            and the WhatsApp reply.
        ``label``         — short Arabic label, ≤ 30 chars.
        ``reason``        — internal diagnostic (logged, not shown
                            to the teacher).
    """

    fallback_type: str
    icon: str
    label: str
    reason: str = ""


# ── Vocabulary table ──────────────────────────────────────────────────

_DEFAULTS: dict[str, FallbackCard] = {
    FALLBACK_TYPE_PDF: FallbackCard(
        FALLBACK_TYPE_PDF, "📄", "ملف PDF غير قابل للمعاينة"),
    FALLBACK_TYPE_VIDEO: FallbackCard(
        FALLBACK_TYPE_VIDEO, "🎥", "مقطع فيديو"),
    FALLBACK_TYPE_AUDIO: FallbackCard(
        FALLBACK_TYPE_AUDIO, "🎙", "تسجيل صوتي"),
    FALLBACK_TYPE_IMAGE: FallbackCard(
        FALLBACK_TYPE_IMAGE, "📷", "صورة غير متاحة"),
    FALLBACK_TYPE_URL: FallbackCard(
        FALLBACK_TYPE_URL, "🔗", "رابط إثرائي"),
    FALLBACK_TYPE_FILE: FallbackCard(
        FALLBACK_TYPE_FILE, "🗂️", "ملف مرفق"),
}


# ── Mapping helpers ───────────────────────────────────────────────────

def _media_type_to_fallback(media_type: str | None) -> str:
    mt = (media_type or "").lower()
    if mt == MEDIA_PDF or mt == MEDIA_DOCUMENT:
        return FALLBACK_TYPE_PDF
    if mt == MEDIA_VIDEO:
        return FALLBACK_TYPE_VIDEO
    if mt in (MEDIA_AUDIO, MEDIA_VOICE):
        return FALLBACK_TYPE_AUDIO
    if mt == MEDIA_IMAGE:
        return FALLBACK_TYPE_IMAGE
    if mt == MEDIA_URL:
        return FALLBACK_TYPE_URL
    return FALLBACK_TYPE_FILE


# ── Public API ────────────────────────────────────────────────────────

def build_fallback_card(
    media_type: str | None,
    *,
    reason: str = "",
) -> FallbackCard:
    """Return the canonical fallback card for ``media_type``.

    ``reason`` is attached for logging / observability — it is **not**
    shown to the teacher (those messages live in
    ``app.services.whatsapp_messages``).
    """
    key = _media_type_to_fallback(media_type)
    base = _DEFAULTS[key]
    if reason:
        return FallbackCard(
            fallback_type=base.fallback_type,
            icon=base.icon,
            label=base.label,
            reason=reason,
        )
    return base
