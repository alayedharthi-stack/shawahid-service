"""
storage_engine.validators — defensive checks reused by every storage
operation.

Five validators:

    • validate_storage_path(path)   — path is inside storage root
    • validate_mime_type(mime, allowed=None)
    • validate_file_size(size_bytes, max_mb=None)
    • validate_teacher_scope(path, teacher_id)
    • validate_safe_url(url)        — for outbound media downloads

These helpers raise :class:`StorageValidationError` on failure so the
caller can decide between hard-stop and soft-skip behaviours.
"""
from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlparse

from app.storage_engine.paths import ensure_within_storage_root, storage_root

logger = logging.getLogger(__name__)


class StorageValidationError(ValueError):
    """Raised when a storage validator rejects an input."""


# ──────────────────────────────────────────────────────────────────────────────
# Defaults
# ──────────────────────────────────────────────────────────────────────────────


DEFAULT_MAX_FILE_MB: int = 50

DEFAULT_ALLOWED_MIME: frozenset[str] = frozenset({
    "image/jpeg", "image/jpg", "image/png", "image/webp",
    "image/heic", "image/heif", "image/gif",
    "video/mp4", "video/quicktime", "video/3gpp", "video/x-matroska",
    "audio/mpeg", "audio/mp4", "audio/ogg", "audio/wav",
    "audio/x-wav", "audio/webm", "audio/aac", "audio/x-m4a",
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
})


# ──────────────────────────────────────────────────────────────────────────────
# Validators
# ──────────────────────────────────────────────────────────────────────────────


def validate_storage_path(path: str | Path) -> Path:
    """Validate that ``path`` resolves inside the storage root.

    Returns the resolved :class:`pathlib.Path`. Raises
    :class:`StorageValidationError` for traversal or non-resolvable paths.
    """
    try:
        return ensure_within_storage_root(path)
    except ValueError as exc:
        raise StorageValidationError(str(exc)) from exc


def validate_mime_type(
    mime: str | None,
    allowed: frozenset[str] | set[str] | None = None,
) -> str:
    """Validate that ``mime`` is in ``allowed`` (defaults to
    ``DEFAULT_ALLOWED_MIME``).

    Returns the normalised mime string.
    """
    if not mime:
        raise StorageValidationError("mime_type is required")
    normalised = mime.strip().lower()
    accepted = allowed if allowed is not None else DEFAULT_ALLOWED_MIME
    if normalised not in accepted:
        raise StorageValidationError(f"unsupported mime_type: {normalised!r}")
    return normalised


def validate_file_size(
    size_bytes: int,
    *,
    max_mb: int | None = None,
) -> int:
    """Reject files that are missing, zero-byte, or above the size cap."""
    if size_bytes is None:
        raise StorageValidationError("file_size is required")
    if size_bytes <= 0:
        raise StorageValidationError("file is empty")
    cap = (max_mb or DEFAULT_MAX_FILE_MB) * 1024 * 1024
    if size_bytes > cap:
        raise StorageValidationError(
            f"file too large: {size_bytes} bytes > cap {cap}"
        )
    return size_bytes


def validate_teacher_scope(path: str | Path, teacher_id: int) -> Path:
    """Validate that ``path`` lives under ``teachers/{teacher_id}/``.

    Used as the last line of defence before serving / deleting a file
    on behalf of a particular teacher.
    """
    if not isinstance(teacher_id, int) or teacher_id <= 0:
        raise StorageValidationError(f"invalid teacher_id: {teacher_id!r}")
    safe = validate_storage_path(path)
    rel = safe.relative_to(storage_root())
    parts = rel.parts
    if len(parts) < 2 or parts[0] != "teachers" or parts[1] != str(teacher_id):
        raise StorageValidationError(
            f"path {safe!s} is not inside teachers/{teacher_id}/"
        )
    return safe


def validate_safe_url(url: str | None) -> str:
    """Reject obviously unsafe outbound URLs.

    Allows only ``http`` and ``https`` schemes with a non-empty host.
    Used by the file_store before downloading a remote media URL.
    """
    if not url:
        raise StorageValidationError("url is required")
    try:
        p = urlparse(url.strip())
    except Exception as exc:
        raise StorageValidationError(f"unparseable url: {url!r}") from exc
    if p.scheme not in ("http", "https"):
        raise StorageValidationError(f"unsupported scheme: {p.scheme!r}")
    if not p.netloc:
        raise StorageValidationError(f"missing host in url: {url!r}")
    return url
