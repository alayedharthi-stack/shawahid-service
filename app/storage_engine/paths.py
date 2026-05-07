"""
storage_engine.paths — single source of truth for every file path
created or accepted by Shawahid AI.

Every other module that needs a file path MUST go through one of:

    • ``build_teacher_storage_path(...)``   for *writing* new files.
    • ``safe_filename(...)``                for normalising user filenames.
    • ``ensure_within_storage_root(...)``   for *reading* an arbitrary path.

Hard rules (enforced by Phase-7 architectural tests):
    • No path is built outside this module (cleanup, file_store,
      evidence_store all delegate here).
    • All produced paths live below the configured storage root.
    • No ``..`` traversal, no absolute paths from user input,
      no Windows reserved names.
    • Filenames are kept human-recognisable: extension preserved,
      Arabic preserved, only filesystem-hostile characters stripped.
"""
from __future__ import annotations

import logging
import mimetypes
import re
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

# Whitelist of media types accepted by build_teacher_storage_path.
# Anything else is bucketed under "misc".
_VALID_MEDIA_TYPES: frozenset[str] = frozenset({
    "image", "video", "audio", "voice", "pdf",
    "document", "url", "text", "evidences", "exports",
    "previews", "thumbnails", "misc",
})

# Filesystem-hostile characters that must always be stripped from filenames.
# We keep Arabic, Latin, digits, hyphen, underscore and dot.
_DANGEROUS_FILENAME_CHARS = re.compile(r"[\x00-\x1f<>:\"/\\|?*]")
# Multiple dots/spaces collapsed.
_RUN_OF_DOTS = re.compile(r"\.{2,}")
_RUN_OF_SPACES = re.compile(r"\s+")
# Windows reserved names (case-insensitive).
_WINDOWS_RESERVED = frozenset({
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
})


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────


def storage_root() -> Path:
    """Resolved absolute storage root. Created on demand."""
    root = settings.storage_path
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def safe_filename(
    original: str | None,
    mime_type: str | None = None,
    *,
    keep_arabic: bool = True,
) -> str:
    """Return a filesystem-safe filename derived from ``original``.

    Always returns a non-empty string with a usable extension. Adds a
    random uuid prefix to guarantee collision-resistance even if the
    teacher uploads two files with the same name.

    Examples
    --------
    >>> safe_filename("الخطة الأسبوعية.pdf")
    'a1b2c3d4_الخطة_الأسبوعية.pdf'
    >>> safe_filename("../../etc/passwd")
    'a1b2c3d4_etc_passwd'
    >>> safe_filename(None, mime_type="image/jpeg")
    'a1b2c3d4_file.jpg'
    """
    uid = uuid.uuid4().hex[:8]

    name = (original or "").strip()
    # Strip path components so callers cannot smuggle "../foo" through.
    name = Path(name).name
    # NFC normalisation keeps Arabic compact.
    name = unicodedata.normalize("NFC", name)

    # Extract extension early so we can preserve it.
    ext = Path(name).suffix.lower()
    stem = Path(name).stem if name else ""

    # Drop dangerous characters; collapse runs of dots/whitespace.
    stem = _DANGEROUS_FILENAME_CHARS.sub("_", stem)
    stem = _RUN_OF_DOTS.sub("_", stem)
    stem = _RUN_OF_SPACES.sub("_", stem)
    stem = stem.strip(" ._-")

    if not keep_arabic:
        stem = re.sub(r"[^\w\-.]", "_", stem, flags=re.ASCII)

    # Reject Windows reserved names by prefixing them.
    if stem.lower() in _WINDOWS_RESERVED:
        stem = f"_{stem}"

    if not stem:
        stem = "file"

    if not ext and mime_type:
        ext = mimetypes.guess_extension(mime_type) or ""

    # Cap stem length so the full name stays under the 255 byte limit.
    stem = stem[:80]

    return f"{uid}_{stem}{ext}"


def build_teacher_storage_path(
    teacher_id: int,
    media_type: str | None,
    filename: str | None,
    *,
    mime_type: str | None = None,
    partition_by_date: bool = True,
    use_legacy_evidences_layout: bool = False,
    now: datetime | None = None,
) -> Path:
    """Build the canonical absolute storage path for a teacher's file.

    Layout (default)::

        {storage_root}/teachers/{teacher_id}/{media_type}/{yyyy}/{mm}/{safe_filename}

    Layout (``use_legacy_evidences_layout=True``)::

        {storage_root}/teachers/{teacher_id}/evidences/{safe_filename}

    The legacy layout exists exclusively so the historic
    ``services.storage.download_and_save`` keeps writing alongside
    pre-Phase-7 rows. New code paths should always use the rich layout.
    """
    if not isinstance(teacher_id, int) or teacher_id <= 0:
        raise ValueError(f"teacher_id must be a positive int, got {teacher_id!r}")

    bucket = (media_type or "misc").strip().lower()
    if bucket not in _VALID_MEDIA_TYPES:
        bucket = "misc"

    name = safe_filename(filename, mime_type=mime_type)

    teacher_root = storage_root() / "teachers" / str(teacher_id)

    if use_legacy_evidences_layout:
        full = teacher_root / "evidences" / name
    elif partition_by_date:
        ts = (now or datetime.now(timezone.utc))
        full = teacher_root / bucket / f"{ts.year:04d}" / f"{ts.month:02d}" / name
    else:
        full = teacher_root / bucket / name

    full.parent.mkdir(parents=True, exist_ok=True)

    # Final safety net: ensure the resolved path lives inside storage root.
    full = ensure_within_storage_root(full)
    return full


def ensure_within_storage_root(path: str | Path) -> Path:
    """Resolve ``path`` and assert it lives inside the storage root.

    Raises ``ValueError`` for path-traversal attempts. Used by every
    read/write helper before touching the filesystem.
    """
    p = Path(path)
    # Make sure the path is absolute; relative paths are treated as relative
    # to the storage root by convention.
    if not p.is_absolute():
        p = storage_root() / p

    try:
        resolved = p.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"Could not resolve path: {p!r}: {exc}") from exc

    root = storage_root()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise ValueError(
            f"Path escapes storage root: {resolved!r} not inside {root!r}"
        )
    return resolved


def teacher_root(teacher_id: int) -> Path:
    """Convenience helper — returns ``{root}/teachers/{teacher_id}/`` and
    creates it on demand. Always validated to live inside storage root.
    """
    if not isinstance(teacher_id, int) or teacher_id <= 0:
        raise ValueError(f"teacher_id must be positive int: {teacher_id!r}")
    p = storage_root() / "teachers" / str(teacher_id)
    p.mkdir(parents=True, exist_ok=True)
    return ensure_within_storage_root(p)


def relative_to_storage_root(path: str | Path) -> str:
    """Return the path as a POSIX string relative to the storage root.

    Useful when serialising paths to logs or DB without leaking absolute
    server paths.
    """
    p = ensure_within_storage_root(path)
    rel = p.relative_to(storage_root())
    return rel.as_posix()
