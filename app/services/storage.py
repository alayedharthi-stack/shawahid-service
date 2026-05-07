import io
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
            # Auto-correct EXIF orientation for images so they render upright in PDF
            raw_bytes = _correct_image_orientation(raw_bytes, mime_type)
            dest_path.write_bytes(raw_bytes)
    except Exception as exc:
        logger.error("Failed to download media for teacher %d from %s: %s", teacher_id, media_url, exc)
        raise

    content_hash = hashlib.sha256(raw_bytes).hexdigest()
    return str(dest_path), safe_name, content_hash


_IMAGE_MIME_PREFIXES = ("image/jpeg", "image/jpg", "image/png", "image/webp", "image/heic", "image/heif")
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}


def _correct_image_orientation(raw_bytes: bytes, mime_type: str | None) -> bytes:
    """Apply EXIF-based auto-rotation using Pillow so images appear upright in PDF.

    Only operates on supported image types.  Returns the original bytes unchanged
    if Pillow is unavailable or the file is not a recognisable image.
    Saves as JPEG (quality=92) to preserve detail while stripping EXIF orientation.
    """
    mt = (mime_type or "").lower()
    is_image_mime = any(mt.startswith(p) for p in _IMAGE_MIME_PREFIXES)
    if not is_image_mime:
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


def extract_pdf_text(file_path: str | Path, max_chars: int = 2000) -> str | None:
    """
    Extract plain text from a PDF file using pdfplumber.

    Returns up to max_chars characters, or None if:
    - File is not a readable PDF
    - PDF is scan-only (no embedded text layer)
    - pdfplumber is not installed

    Logs: [PDF TEXT EXTRACTED] or [PDF NO TEXT] or [PDF EXTRACT ERROR]
    """
    path = Path(file_path)
    if not path.exists():
        logger.warning("[PDF EXTRACT ERROR] file not found: %s", path)
        return None

    try:
        import pdfplumber

        text_parts: list[str] = []
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                if page_text.strip():
                    text_parts.append(page_text.strip())
                if sum(len(p) for p in text_parts) >= max_chars:
                    break

        full_text = "\n".join(text_parts).strip()
        if not full_text:
            logger.info("[PDF NO TEXT] file=%s — likely a scanned image PDF", path.name)
            return None

        # Truncate to max_chars
        if len(full_text) > max_chars:
            full_text = full_text[:max_chars] + "…"

        logger.info(
            "[PDF TEXT EXTRACTED] file=%s chars=%d pages_read=%d",
            path.name, len(full_text), len(text_parts),
        )
        return full_text

    except ImportError:
        logger.warning("[PDF EXTRACT ERROR] pdfplumber not installed — cannot extract text from PDF")
        return None
    except Exception as exc:
        logger.warning("[PDF EXTRACT ERROR] file=%s error=%s", path.name if path else "?", exc)
        return None


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
