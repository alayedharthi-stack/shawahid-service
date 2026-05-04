import logging
import mimetypes
import uuid
from pathlib import Path

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


def _safe_filename(original: str | None, mime_type: str | None) -> str:
    """Generate a safe ASCII filename to avoid path/encoding issues."""
    uid = uuid.uuid4().hex[:12]
    ext = ""
    if original:
        ext = Path(original).suffix
    if not ext and mime_type:
        ext = mimetypes.guess_extension(mime_type) or ""
    return f"file_{uid}{ext}"


async def download_and_save(
    teacher_id: int,
    media_url: str,
    original_filename: str | None = None,
    mime_type: str | None = None,
) -> tuple[str, str]:
    """
    Download media from media_url and save under teacher's isolated folder.
    Returns (storage_path, safe_filename).
    Never stores files outside the teacher's own directory.
    """
    safe_name = _safe_filename(original_filename, mime_type)
    dest_dir = settings.evidence_storage(teacher_id)
    dest_path = dest_dir / safe_name

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(media_url)
            resp.raise_for_status()
            dest_path.write_bytes(resp.content)
    except Exception as exc:
        logger.error("Failed to download media for teacher %d from %s: %s", teacher_id, media_url, exc)
        raise

    return str(dest_path), safe_name


def detect_evidence_type(mime_type: str | None, file_name: str | None, text: str | None) -> str:
    if not mime_type and not file_name:
        return "text"
    mt = (mime_type or "").lower()
    fn = (file_name or "").lower()
    if mt.startswith("image/"):
        return "image"
    if mt == "application/pdf" or fn.endswith(".pdf"):
        return "pdf"
    if mt.startswith("video/"):
        return "video"
    if mt in ("application/msword", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"):
        return "document"
    if fn.endswith((".doc", ".docx")):
        return "document"
    return "document"
