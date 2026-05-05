import logging
import mimetypes
import re
import uuid
from pathlib import Path

import httpx

_URL_RE = re.compile(r'https?://\S+', re.IGNORECASE)


def extract_urls(text: str | None) -> list[str]:
    """Return all HTTP/HTTPS URLs found in text."""
    if not text:
        return []
    return _URL_RE.findall(text)

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
    auth_token: str | None = None,
) -> tuple[str, str, str]:
    """
    Download media from media_url and save under teacher's isolated folder.
    Returns (storage_path, safe_filename, content_hash).
    Never stores files outside the teacher's own directory.
    Pass auth_token for Meta Cloud API media URLs that require Authorization.
    """
    import hashlib

    safe_name = _safe_filename(original_filename, mime_type)
    dest_dir = settings.evidence_storage(teacher_id)
    dest_path = dest_dir / safe_name

    headers = {}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(media_url, headers=headers)
            resp.raise_for_status()
            raw_bytes = resp.content
            dest_path.write_bytes(raw_bytes)
    except Exception as exc:
        logger.error("Failed to download media for teacher %d from %s: %s", teacher_id, media_url, exc)
        raise

    content_hash = hashlib.sha256(raw_bytes).hexdigest()
    return str(dest_path), safe_name, content_hash


def detect_evidence_type(mime_type: str | None, file_name: str | None, text: str | None) -> str:
    if not mime_type and not file_name:
        if text and _URL_RE.search(text):
            return "url"
        return "text"
    mt = (mime_type or "").lower()
    fn = (file_name or "").lower()
    if mt.startswith("image/"):
        return "image"
    if mt == "application/pdf" or fn.endswith(".pdf"):
        return "pdf"
    if mt.startswith("video/"):
        return "video"
    if mt.startswith("audio/") or fn.endswith((".ogg", ".mp3", ".m4a", ".wav", ".opus")):
        return "audio"
    if mt in ("application/msword", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"):
        return "document"
    if fn.endswith((".doc", ".docx")):
        return "document"
    return "document"
