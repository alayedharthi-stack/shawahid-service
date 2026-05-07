"""
Image pipeline.

Responsibilities
    • Validate that a path on disk is a recognised image type.
    • Correct EXIF orientation (kept here as a thin re-export of the
      existing helper in ``app.services.storage`` to avoid duplicating
      Pillow code; behaviour is unchanged).
    • Encode an image as a ``data:image/...`` URI safe to embed in
      PDFs / HTML.

Hard rules:
    • No ORM / DB.
    • Only this module + ``_base64_utils`` may produce ``data:`` URIs
      for image content.
"""
from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

from app.media_engine._base64_utils import (
    bytes_to_data_uri,
    file_to_data_uri,
)

logger = logging.getLogger(__name__)

IMAGE_EXTS: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic", ".heif"}
)
IMAGE_MIME_PREFIXES: tuple[str, ...] = (
    "image/jpeg", "image/jpg", "image/png", "image/webp",
    "image/gif", "image/heic", "image/heif",
)


def is_image_path(path: str | Path | None) -> bool:
    if not path:
        return False
    return Path(path).suffix.lower() in IMAGE_EXTS


def is_image_mime(mime_type: str | None) -> bool:
    if not mime_type:
        return False
    mt = mime_type.lower()
    return any(mt.startswith(p) for p in IMAGE_MIME_PREFIXES)


def image_to_data_uri(path: str | Path | None) -> str | None:
    """Inline an image file as a base64 data URI.

    Returns ``None`` when the path is missing, not an image, or larger
    than the engine's inline cap. The caller is expected to fall back
    to a ``public_url`` or a placeholder card.
    """
    if not is_image_path(path):
        return None
    return file_to_data_uri(str(path))


def svg_text_to_data_uri(svg_text: str) -> str:
    """Encode an in-memory SVG document as ``data:image/svg+xml;base64,...``

    Callers (e.g. the ministry-logo helper in ``exporter.py``) used to
    do this by hand. Centralising the encoding keeps Phase-4's "no
    base64 outside media_engine" rule enforceable.
    """
    return bytes_to_data_uri(svg_text.encode("utf-8"), "image/svg+xml")


def guess_image_mime(path: str | Path | None) -> str | None:
    """Best-effort MIME for *known image* paths only.

    Returns ``None`` for non-image extensions so callers don't
    accidentally label a PDF as ``image/jpeg``.
    """
    if not is_image_path(path):
        return None
    return (
        mimetypes.guess_type(Path(path).name)[0]
        or "image/jpeg"
    )
