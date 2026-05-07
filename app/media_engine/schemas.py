"""
MediaAsset DTO — the single shape every consumer of media_engine reads.

Hard rules:
    • No ORM, no DB, no service-layer imports.
    • Pure Python dataclass — safe to construct in tests without I/O.
    • Phase-4 contract: any module that needs to *describe* a media
      file must do so through this DTO; raw paths and ad-hoc dicts are
      banned across module boundaries.
"""
from __future__ import annotations

from dataclasses import dataclass


# ── Canonical media-type vocabulary ───────────────────────────────────
MEDIA_IMAGE = "image"
MEDIA_VIDEO = "video"
MEDIA_AUDIO = "audio"
MEDIA_VOICE = "voice"
MEDIA_PDF = "pdf"
MEDIA_DOCUMENT = "document"
MEDIA_URL = "url"
MEDIA_TEXT = "text"

ALL_MEDIA_TYPES: tuple[str, ...] = (
    MEDIA_IMAGE, MEDIA_VIDEO, MEDIA_AUDIO, MEDIA_VOICE,
    MEDIA_PDF, MEDIA_DOCUMENT, MEDIA_URL, MEDIA_TEXT,
)


# ── Fallback vocabulary ───────────────────────────────────────────────
# When we cannot read the underlying file (missing / corrupt / unsupported)
# we still return a canonical "card descriptor" so the caller can render
# something instead of crashing. The constants below are the only valid
# values for ``MediaAsset.fallback_type``.
FALLBACK_TYPE_PDF = "pdf_fallback"
FALLBACK_TYPE_VIDEO = "video_fallback"
FALLBACK_TYPE_AUDIO = "audio_fallback"
FALLBACK_TYPE_IMAGE = "image_fallback"
FALLBACK_TYPE_URL = "url_fallback"
FALLBACK_TYPE_FILE = "file_fallback"


@dataclass
class MediaAsset:
    """The complete description of a single media artefact.

    Created by the pipelines in ``image_pipeline`` / ``pdf_pipeline``
    / ``video_pipeline`` / ``audio_pipeline``. Consumers read only —
    never mutate.

    Field semantics
    ---------------
    * ``media_type``  : one of the ``MEDIA_*`` constants above.
    * ``file_path``   : absolute path on local disk, or ``None`` when
                        the asset is a URL or a text-only evidence.
    * ``public_url``  : long-lived ``/files/...`` URL the user can open.
                        ``None`` if the file is not publicly served
                        (e.g. only available behind /media/{id}).
    * ``preview_url`` : ``data:`` URI or ``/files/`` URL of a render
                        suitable for inline embedding in HTML / PDF.
                        For PDFs this is the first-page rasterisation.
    * ``thumbnail_url``: small representation suited for compact cards.
                        Often equal to ``preview_url`` for images and
                        videos. ``None`` for audio / text / URL.
    * ``player_url``  : the URL the WhatsApp viewer or PDF button
                        targets when the user wants to *play* / *open*
                        the asset (videos, audio, PDF).
    * ``fallback_type``: filled when at least one of the URLs above is
                        missing or invalid. The renderer uses this to
                        pick the right placeholder card.
    """

    media_type: str
    file_path: str | None = None
    public_url: str | None = None
    preview_url: str | None = None
    thumbnail_url: str | None = None
    player_url: str | None = None
    mime_type: str | None = None
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    file_size: int | None = None
    has_preview: bool = False
    has_thumbnail: bool = False
    fallback_type: str | None = None

    # ── Convenience predicates ─────────────────────────────────────────

    @property
    def is_visual(self) -> bool:
        """True for assets that render as a picture (image, video thumb,
        PDF preview). Used by the renderer to pick the right card."""
        return self.media_type in (MEDIA_IMAGE, MEDIA_VIDEO, MEDIA_PDF)

    @property
    def has_fallback(self) -> bool:
        return bool(self.fallback_type)
