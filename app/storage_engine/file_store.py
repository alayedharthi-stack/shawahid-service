"""
storage_engine.file_store — every byte that lands on disk passes here.

Public surface:
    save_uploaded_file        — write bytes safely under a teacher
    save_uploaded_file_legacy — write bytes using legacy evidences/ layout
    download_and_save         — download a remote URL into a teacher folder
    read_stored_file          — read raw bytes (path-traversal safe)
    delete_stored_file        — remove a single file
    file_exists / get_file_size — lightweight predicates

Hard rules:
    • Every path argument is run through ``ensure_within_storage_root``
      before any I/O operation. There is no other way to read or write.
    • All write helpers compute and return ``StoredFile`` so the caller
      always has the canonical hash + size + public_path in one shot.
"""
from __future__ import annotations

import io
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.storage_engine.hashing import compute_content_hash
from app.storage_engine.paths import (
    build_teacher_storage_path,
    ensure_within_storage_root,
    storage_root,
)
from app.storage_engine.schemas import StoredFile

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────────────


_IMAGE_MIME_PREFIXES = ("image/jpeg", "image/jpg", "image/png", "image/webp", "image/heic", "image/heif")


def _correct_image_orientation(raw_bytes: bytes, mime_type: str | None) -> bytes:
    """Apply EXIF auto-rotation. Mirrors the legacy helper from
    ``services.storage`` so the WhatsApp upload pipeline keeps producing
    upright images.
    """
    mt = (mime_type or "").lower()
    if not any(mt.startswith(p) for p in _IMAGE_MIME_PREFIXES):
        return raw_bytes
    try:
        from PIL import Image, ImageOps
        img = Image.open(io.BytesIO(raw_bytes))
        img = ImageOps.exif_transpose(img)
        buf = io.BytesIO()
        save_fmt = "JPEG" if img.mode in ("RGB", "L") else "PNG"
        if img.mode not in ("RGB", "RGBA", "L"):
            img = img.convert("RGB")
            save_fmt = "JPEG"
        img.save(buf, format=save_fmt, quality=92)
        return buf.getvalue()
    except Exception as exc:
        logger.warning("EXIF transpose failed, using original bytes: %s", exc)
        return raw_bytes


def _public_path_for(stored_path: Path) -> str | None:
    """Convert an absolute storage path into the ``/files/...`` URL the
    media_engine serves.
    """
    try:
        rel = stored_path.relative_to(storage_root())
    except ValueError:
        return None
    parts = rel.parts
    if len(parts) >= 2 and parts[0] == "teachers":
        return "/files/" + "/".join(parts[1:])
    return None


def _build_stored_file(
    *,
    raw_bytes: bytes,
    stored_path: Path,
    original_filename: str | None,
    mime_type: str | None,
    storage_bucket: str,
) -> StoredFile:
    return StoredFile(
        original_filename=original_filename,
        stored_path=str(stored_path),
        public_path=_public_path_for(stored_path),
        mime_type=mime_type,
        file_size=len(raw_bytes),
        content_hash=compute_content_hash(raw_bytes),
        storage_bucket=storage_bucket,
        created_at=datetime.now(timezone.utc),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Write helpers
# ──────────────────────────────────────────────────────────────────────────────


def save_uploaded_file(
    *,
    teacher_id: int,
    raw_bytes: bytes,
    original_filename: str | None,
    mime_type: str | None,
    media_type: str | None = None,
    correct_image_orientation: bool = True,
) -> StoredFile:
    """Persist ``raw_bytes`` under the teacher using the rich layout
    ``teachers/{id}/{media_type}/{yyyy}/{mm}/{safe_filename}``.

    Returns a fully populated :class:`StoredFile`.
    """
    if correct_image_orientation:
        raw_bytes = _correct_image_orientation(raw_bytes, mime_type)

    bucket = (media_type or "misc").strip().lower()
    target = build_teacher_storage_path(
        teacher_id=teacher_id,
        media_type=bucket,
        filename=original_filename,
        mime_type=mime_type,
    )
    target.write_bytes(raw_bytes)
    return _build_stored_file(
        raw_bytes=raw_bytes,
        stored_path=target,
        original_filename=original_filename,
        mime_type=mime_type,
        storage_bucket=bucket,
    )


def save_uploaded_file_legacy(
    *,
    teacher_id: int,
    raw_bytes: bytes,
    original_filename: str | None,
    mime_type: str | None,
    correct_image_orientation: bool = True,
) -> StoredFile:
    """Persist using the historical ``teachers/{id}/evidences/`` layout.

    This exists ONLY to keep ``services.storage.download_and_save`` writing
    alongside pre-Phase-7 rows. New code paths should use
    :func:`save_uploaded_file` instead.
    """
    if correct_image_orientation:
        raw_bytes = _correct_image_orientation(raw_bytes, mime_type)

    target = build_teacher_storage_path(
        teacher_id=teacher_id,
        media_type="evidences",
        filename=original_filename,
        mime_type=mime_type,
        use_legacy_evidences_layout=True,
    )
    target.write_bytes(raw_bytes)
    return _build_stored_file(
        raw_bytes=raw_bytes,
        stored_path=target,
        original_filename=original_filename,
        mime_type=mime_type,
        storage_bucket="evidences",
    )


async def download_and_save(
    *,
    teacher_id: int,
    media_url: str,
    original_filename: str | None = None,
    mime_type: str | None = None,
    auth_token: str | None = None,
    timeout_seconds: int = 30,
    use_legacy_layout: bool = True,
) -> StoredFile:
    """Download a remote URL and write the bytes under the teacher folder.

    ``use_legacy_layout=True`` (the default) preserves the historical
    ``teachers/{id}/evidences/`` layout used by the WhatsApp webhook so
    existing DB rows stay valid. Set it to ``False`` for new code paths
    that want the rich ``{media_type}/{yyyy}/{mm}/`` partitioning.
    """
    headers: dict[str, str] = {}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
        resp = await client.get(media_url, headers=headers)
        resp.raise_for_status()
        raw_bytes = resp.content

    if use_legacy_layout:
        return save_uploaded_file_legacy(
            teacher_id=teacher_id,
            raw_bytes=raw_bytes,
            original_filename=original_filename,
            mime_type=mime_type,
        )
    return save_uploaded_file(
        teacher_id=teacher_id,
        raw_bytes=raw_bytes,
        original_filename=original_filename,
        mime_type=mime_type,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Read / inspect / delete
# ──────────────────────────────────────────────────────────────────────────────


def read_stored_file(path: str | Path) -> bytes:
    """Read raw bytes of a file inside the storage root.

    Raises ``ValueError`` for paths that escape the root, ``FileNotFoundError``
    when the file does not exist.
    """
    safe = ensure_within_storage_root(path)
    if not safe.exists():
        raise FileNotFoundError(f"Storage file not found: {safe}")
    if not safe.is_file():
        raise FileNotFoundError(f"Storage path is not a file: {safe}")
    return safe.read_bytes()


def file_exists(path: str | Path | None) -> bool:
    """Return ``True`` if ``path`` lives inside the storage root and exists.

    Returns ``False`` for ``None``, escaping paths, missing files, or
    directory-only paths.
    """
    if not path:
        return False
    try:
        safe = ensure_within_storage_root(path)
    except ValueError:
        return False
    return safe.is_file()


def get_file_size(path: str | Path) -> int:
    """Return file size in bytes. Raises if the path escapes or is missing."""
    safe = ensure_within_storage_root(path)
    if not safe.exists() or not safe.is_file():
        raise FileNotFoundError(f"Storage file not found: {safe}")
    return safe.stat().st_size


def delete_stored_file(path: str | Path) -> bool:
    """Remove a single file from storage. Returns ``True`` on success,
    ``False`` if the file was already missing.

    Raises ``ValueError`` if the path escapes the storage root.
    """
    safe = ensure_within_storage_root(path)
    if not safe.exists():
        return False
    if not safe.is_file():
        raise ValueError(f"Refusing to delete non-file: {safe}")
    try:
        safe.unlink()
    except OSError:
        # Best-effort retry once after a tiny pause (Windows file-lock case).
        time.sleep(0.05)
        safe.unlink()
    return True
