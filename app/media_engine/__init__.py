"""
media_engine — single source of truth for every media operation.
────────────────────────────────────────────────────────────────

Phase 4 contract
================
Anything that touches a teacher's image / PDF / video / audio file —
reading, encoding, building a public URL, generating a preview or
thumbnail, or producing a fallback card — must live in this package.

The rest of the app calls into media_engine through a small, stable
public surface and never touches ``base64``, ``mimetypes``, or
``Path.read_bytes`` directly.

Hard rules (enforced by the Phase-4 test suite):

    • No SQLAlchemy / ORM imports.
    • No Playwright imports.
    • No ``base64.b64encode`` calls outside this package.
    • No ``data:`` URI construction outside this package.
    • No public-URL building outside this package.

Public API
==========
The convenience re-exports below are stable; downstream modules
should import from ``app.media_engine`` not from internal sub-modules.
"""
from __future__ import annotations

from app.media_engine.fallback_media import (
    FallbackCard,
    build_fallback_card,
)
from app.media_engine.media_urls import (
    MediaUrls,
    build_media_urls,
    storage_path_to_public_url,
)
from app.media_engine.preview_generator import build_preview
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
    MEDIA_TEXT,
    MEDIA_URL,
    MEDIA_VIDEO,
    MEDIA_VOICE,
    MediaAsset,
)
from app.media_engine.thumbnail_generator import build_thumbnail

__all__ = [
    # DTOs
    "MediaAsset",
    "MediaUrls",
    "FallbackCard",
    # constants
    "MEDIA_IMAGE", "MEDIA_PDF", "MEDIA_DOCUMENT", "MEDIA_VIDEO",
    "MEDIA_AUDIO", "MEDIA_VOICE", "MEDIA_URL", "MEDIA_TEXT",
    "FALLBACK_TYPE_PDF", "FALLBACK_TYPE_VIDEO", "FALLBACK_TYPE_AUDIO",
    "FALLBACK_TYPE_IMAGE", "FALLBACK_TYPE_URL", "FALLBACK_TYPE_FILE",
    # public API
    "build_media_urls",
    "build_preview",
    "build_thumbnail",
    "build_fallback_card",
    "storage_path_to_public_url",
]
