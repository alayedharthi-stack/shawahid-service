"""
Internal: the **only** module in the codebase allowed to call
``base64.b64encode`` or to construct a ``data:`` URI.

A test asserts this rule (``test_no_base64_outside_media_engine``).
External code asks for a data URI through the public pipeline
helpers (``image_pipeline.image_to_data_uri`` etc.) which delegate
here.

Rationale
=========
Phase-4 plan flagged scattered base64 conversions as the #1 source
of memory bloat and PDF size regressions. Centralising them lets us:

    • Cache repeated reads of the same path.
    • Cap the maximum embeddable size in one place.
    • Add observability (``logger.info`` on every encode).
"""
from __future__ import annotations

import base64
import logging
import mimetypes
from pathlib import Path

logger = logging.getLogger(__name__)

# Hard cap to avoid embedding multi-megabyte assets into PDFs.
# Files larger than this fall back to a public URL or a placeholder.
_MAX_INLINE_BYTES = 8 * 1024 * 1024  # 8 MiB


def file_to_data_uri(
    path: str | Path | None,
    mime_type: str | None = None,
) -> str | None:
    """Read ``path`` and return ``data:<mime>;base64,<...>`` on success.

    Returns ``None`` for missing / unreadable files. Logs a warning
    so the upstream pipeline can decide whether to emit a fallback.
    """
    if not path:
        return None

    file_path = Path(path)
    if not file_path.exists() or not file_path.is_file():
        logger.warning("[MEDIA] missing file: %s", path)
        return None

    try:
        size = file_path.stat().st_size
    except OSError as exc:
        logger.warning("[MEDIA] stat failed for %s: %s", path, exc)
        return None

    if size > _MAX_INLINE_BYTES:
        logger.info(
            "[MEDIA] %s exceeds inline cap (%d bytes) — caller should "
            "fall back to a public URL", path, size,
        )
        return None

    guessed = (
        mime_type
        or mimetypes.guess_type(file_path.name)[0]
        or "application/octet-stream"
    )

    try:
        encoded = base64.b64encode(file_path.read_bytes()).decode("ascii")
    except Exception as exc:
        logger.warning("[MEDIA] read failed for %s: %s", path, exc)
        return None

    return f"data:{guessed};base64,{encoded}"


def bytes_to_data_uri(payload: bytes, mime_type: str) -> str:
    """Encode an in-memory blob (e.g. a generated SVG) as a data URI.

    Unlike :func:`file_to_data_uri` this never returns ``None`` — the
    caller must guarantee the inputs are valid bytes.
    """
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"
