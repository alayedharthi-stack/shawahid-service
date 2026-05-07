"""
The single URL factory for the entire app.

Phase-4 contract: every consumer (exporter, webhook, future
review_engine) **must** call :func:`build_media_urls` instead of
constructing URLs by hand. The exporter no longer prefixes
``/files/...`` itself; the webhook no longer formats the
``/media/{id}`` viewer link by hand.

Hard rules:
    • No ORM / DB / Playwright.
    • No HTTP calls.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from app.media_engine.image_pipeline import image_to_data_uri
from app.media_engine.video_pipeline import thumbnail_to_data_uri

logger = logging.getLogger(__name__)


# ── DTO ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MediaUrls:
    """The four URL slots every evidence supports.

    Values are strings (or ``None``):
        * ``public_url``   — long-lived ``/files/...`` link.
        * ``preview_url``  — inline ``data:`` URI suitable for PDF.
        * ``thumbnail_url``— small render for compact cards.
        * ``player_url``   — link the WhatsApp viewer / PDF button
                             targets (e.g. ``/media/{id}``).
    """

    public_url: str | None = None
    preview_url: str | None = None
    thumbnail_url: str | None = None
    player_url: str | None = None


# ── Hostnames we never trust ──────────────────────────────────────────
# These domains return temporary / auth-gated media URLs that 401
# for end users, so we never expose them in public links.
_BLOCKED_HOSTS: tuple[str, ...] = (
    "lookaside.fbsbx.com",
    "lookaside.facebook.com",
    "fbsbx.com",
    "fbcdn.net",
    "facebook.com",
    "graph.facebook.com",
    "whatsapp.net",
    "whatsapp.com",
    "mmg.whatsapp.net",
)


def is_safe_public_url(url: str | None) -> bool:
    """Return ``False`` for known transient / auth-gated CDN links."""
    if not url:
        return False
    lowered = url.strip().lower()
    if not lowered.startswith(("http://", "https://")):
        return False
    return not any(host in lowered for host in _BLOCKED_HOSTS)


def storage_path_to_public_url(
    storage_path: str | None,
    base_url: str,
) -> str | None:
    """Translate a teacher-isolated storage path to a public ``/files``
    URL.

    Mirrors :func:`app.services.storage.storage_path_to_file_url` but
    lives here so callers don't import from ``services/storage``.
    """
    if not storage_path:
        return None
    try:
        parts = Path(storage_path).parts
        idx = next(i for i, p in enumerate(parts) if p == "teachers")
    except StopIteration:
        logger.debug(
            "[MEDIA URL] 'teachers' segment missing from %s", storage_path
        )
        return None
    rel = "/".join(parts[idx:])
    return f"{base_url.rstrip('/')}/files/{rel}"


def storage_root_to_public_url(
    path: str | None,
    *,
    storage_root: Path,
    base_url: str,
) -> str | None:
    """Variant that uses ``Path.relative_to(storage_root)``.

    Used by the exporter, which historically resolved paths against
    ``settings.storage_path`` instead of the ``teachers/`` segment.
    Both helpers live here so URL construction stays centralised.
    """
    if not path:
        return None
    try:
        rel_path = Path(path).resolve().relative_to(storage_root.resolve())
    except Exception:
        return None
    return f"{base_url.rstrip('/')}/files/{rel_path.as_posix()}"


def public_media_url(
    evidence_type: str | None,
    storage_path: str | None,
    media_url: str | None,
    *,
    storage_root: Path,
    base_url: str,
) -> str | None:
    """Pick the safest public URL for a single evidence.

    Mirrors the historic ``_public_media_url`` in ``exporter.py`` so
    the PDF render output is byte-identical:

        1. Resolve the local ``/files/...`` URL via ``storage_root``.
        2. For videos whose storage path is actually a JPEG thumbnail
           we *suppress* the URL — playing a still as a video crashes
           the WhatsApp viewer.
        3. Fallback to the supplied ``media_url`` only if it passes
           :func:`is_safe_public_url`.
    """
    local = storage_root_to_public_url(
        storage_path, storage_root=storage_root, base_url=base_url,
    )
    if local:
        suffix = Path(storage_path or "").suffix.lower()
        if (evidence_type or "").lower() == "video" and suffix in {
            ".jpg", ".jpeg", ".png", ".gif", ".webp",
        }:
            return None
        return local
    return media_url if is_safe_public_url(media_url) else None


# ── Public factory ────────────────────────────────────────────────────

def build_media_urls(
    *,
    evidence_id: int | None,
    evidence_type: str | None,
    storage_path: str | None,
    media_url: str | None,
    base_url: str,
) -> MediaUrls:
    """Compute every URL the renderer / WhatsApp layer needs.

    The function is *pure*: same inputs → same outputs, no DB calls.
    Caller pulls the four fields out of an ORM evidence and the
    settings' ``base_url`` and feeds them in.

    Resolution order
    ----------------
    1. ``public_url`` — local ``/files/...`` if the storage path lives
       under the teacher folder; else the verbatim ``media_url`` only
       when it passes ``is_safe_public_url``.
    2. ``preview_url`` — inline ``data:`` URI for images and video
       thumbnails. PDF previews are computed by the exporter through
       ``pdf_pipeline.first_page_data_uri`` because they need the
       generated rasterisation; we therefore leave ``preview_url``
       blank for PDFs and let the caller fill it.
    3. ``thumbnail_url`` — same as ``preview_url`` for images / video.
    4. ``player_url`` — always ``<base_url>/media/{id}`` when an id is
       provided, so every WhatsApp viewer URL goes through one route.
    """
    et = (evidence_type or "").lower()
    base = base_url.rstrip("/")

    public_url: str | None = storage_path_to_public_url(
        storage_path, base_url
    )
    if not public_url and is_safe_public_url(media_url):
        public_url = media_url

    # Videos must never expose a still-image as the playable URL even
    # when the storage_path happens to be a JPEG thumbnail.
    if et == "video" and public_url:
        suffix = Path(storage_path or "").suffix.lower()
        if suffix in {".jpg", ".jpeg", ".png"}:
            public_url = None
            if is_safe_public_url(media_url):
                public_url = media_url

    preview_url: str | None = None
    thumbnail_url: str | None = None

    if et == "image":
        preview_url = image_to_data_uri(storage_path)
        thumbnail_url = preview_url
    elif et == "video":
        preview_url = thumbnail_to_data_uri(storage_path)
        thumbnail_url = preview_url
    # PDFs are intentionally left to the exporter / pdf_pipeline so
    # we never call PyMuPDF inside the URL factory.

    player_url: str | None = (
        f"{base}/media/{evidence_id}" if evidence_id is not None else None
    )

    return MediaUrls(
        public_url=public_url,
        preview_url=preview_url,
        thumbnail_url=thumbnail_url,
        player_url=player_url,
    )
