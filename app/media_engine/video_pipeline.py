"""
Video pipeline.

Resolves a stored video evidence to:
    • the absolute path to the actual video file (which may differ
      from ``storage_path`` because that field can hold a thumbnail),
    • the absolute path to a thumbnail (.thumb.jpg) when present,
    • a public URL for the player, and
    • the player URL the WhatsApp viewer should embed.

Hard rules:
    • No ORM / DB / Playwright.
    • All inputs are plain strings or ``Path``s — never an ORM row.
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.media_engine.image_pipeline import image_to_data_uri

logger = logging.getLogger(__name__)

VIDEO_EXTS: tuple[str, ...] = (
    ".mp4", ".mpeg4", ".mov", ".webm", ".avi", ".mkv", ".m4v",
)


def is_video_path(path: str | Path | None) -> bool:
    if not path:
        return False
    return Path(path).suffix.lower() in VIDEO_EXTS


def resolve_video_file(
    storage_path: str | None,
    file_name: str | None = None,
) -> str | None:
    """Return the absolute path to the *actual* video file.

    The webhook stores the thumbnail at ``<stem>.thumb.jpg`` and
    leaves the original video next to it. Different code paths
    historically pointed ``storage_path`` to either the thumbnail or
    the video, so the resolver tolerates both shapes.

    Returns ``None`` if no usable video file can be located.
    """
    if not storage_path:
        return None
    sp = Path(storage_path)

    # Case 1: storage_path is a thumbnail — find the original via file_name.
    if sp.name.endswith(".thumb.jpg") and file_name:
        candidate = sp.parent / file_name
        if candidate.exists():
            return str(candidate)

    # Case 2: storage_path itself is the video.
    if sp.suffix.lower() in VIDEO_EXTS and sp.exists():
        return str(sp)

    # Case 3: derive video stem from "<stem>.thumb.jpg" and try every
    # supported extension.
    if sp.name.endswith(".thumb.jpg"):
        stem = sp.name[: -len(".thumb.jpg")]
        for ext in VIDEO_EXTS:
            candidate = sp.parent / (stem + ext)
            if candidate.exists():
                return str(candidate)

    return None


def resolve_thumbnail_path(storage_path: str | None) -> str | None:
    """Return the absolute path of a video's pre-rendered thumbnail
    on disk, or ``None`` when no thumbnail exists.

    Two storage shapes are supported:

        1. ``<storage_path>_thumb.jpg`` — used by the legacy webhook.
        2. ``<storage_path>`` itself if it is already a JPEG (some
           older code paths persisted the thumbnail in place).
    """
    if not storage_path:
        return None

    legacy = Path(storage_path + "_thumb.jpg")
    if legacy.exists():
        return str(legacy)

    sp = Path(storage_path)
    if sp.exists() and sp.suffix.lower() in {".jpg", ".jpeg", ".png"}:
        return str(sp)
    return None


def thumbnail_to_data_uri(storage_path: str | None) -> str | None:
    """Inline the resolved thumbnail as ``data:image/...``.

    Returns ``None`` when no thumbnail exists or the file cannot be
    read. Callers fall back to the canonical "🎥" video placeholder.
    """
    thumb = resolve_thumbnail_path(storage_path)
    if not thumb:
        return None
    return image_to_data_uri(thumb)
