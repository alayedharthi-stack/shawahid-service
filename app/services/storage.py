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


class PDFExtract:
    """Structured result of a smart PDF extraction."""
    __slots__ = (
        "full_text", "page_count", "pages_with_text", "first_lines",
        "has_tables", "has_questions", "has_objectives", "has_grades_table",
        "has_ministry_header", "detected_keywords",
    )

    def __init__(self):
        self.full_text: str = ""
        self.page_count: int = 0
        self.pages_with_text: int = 0
        self.first_lines: str = ""          # first 5 non-empty lines (title area)
        self.has_tables: bool = False
        self.has_questions: bool = False     # أسئلة / اختيار
        self.has_objectives: bool = False    # أهداف / نواتج التعلم
        self.has_grades_table: bool = False  # درجات / الدرجة / رصد
        self.has_ministry_header: bool = False  # وزارة التعليم
        self.detected_keywords: list[str] = []

    @property
    def is_empty(self) -> bool:
        return not self.full_text.strip()


def extract_pdf_smart(file_path: str | Path, max_chars: int = 3500) -> PDFExtract | None:
    """
    Smart multi-page PDF extraction with document intelligence.

    Strategy:
    - Read all pages and collect text, prioritising early content.
    - Detect structural signals: tables, questions, objectives, grade tables.
    - Return a PDFExtract object with rich metadata for GPT classification.
    - Falls back gracefully: returns None if pdfplumber unavailable or PDF unreadable.

    Logs: [PDF TEXT EXTRACTED] [PDF NO TEXT] [PDF EXTRACT ERROR]
    """
    path = Path(file_path)
    if not path.exists():
        logger.warning("[PDF EXTRACT ERROR] file not found: %s", path)
        return None

    try:
        import pdfplumber
    except ImportError:
        logger.warning("[PDF EXTRACT ERROR] pdfplumber not installed")
        return None

    result = PDFExtract()

    # ── Document intelligence keywords ────────────────────────────────────────
    _OBJECTIVES_KW  = ("هدف", "هدفًا", "نواتج التعلم", "بنهاية الدرس", "الأهداف",
                       "يستطيع الطالب", "يتمكن المتعلم")
    _QUESTIONS_KW   = ("السؤال", "اختر", "أجب", "صح أم خطأ", "اختيار من متعدد",
                       "الدرجة", "نقطة", "علامة")
    _GRADES_KW      = ("الدرجة", "مجموع", "كشف", "رصد", "التقدير", "ممتاز",
                       "جيد جدًا", "جيد", "مقبول", "راسب")
    _MINISTRY_KW    = ("وزارة التعليم", "المملكة العربية السعودية", "إدارة التعليم",
                       "مدير المدرسة", "الرقم الوزاري")

    try:
        with pdfplumber.open(str(path)) as pdf:
            result.page_count = len(pdf.pages)
            page_texts: list[str] = []

            for i, page in enumerate(pdf.pages):
                # Detect tables on this page
                try:
                    if page.extract_tables():
                        result.has_tables = True
                except Exception:
                    pass

                page_text = (page.extract_text() or "").strip()
                if page_text:
                    result.pages_with_text += 1
                    page_texts.append(page_text)

                    # Check document signals
                    lower_text = page_text.lower()
                    if any(kw in page_text for kw in _OBJECTIVES_KW):
                        result.has_objectives = True
                    if any(kw in page_text for kw in _QUESTIONS_KW):
                        result.has_questions = True
                    if any(kw in page_text for kw in _GRADES_KW):
                        result.has_grades_table = True
                    if any(kw in page_text for kw in _MINISTRY_KW):
                        result.has_ministry_header = True

    except Exception as exc:
        logger.warning("[PDF EXTRACT ERROR] file=%s error=%s", path.name, exc)
        return None

    if not page_texts:
        logger.info("[PDF NO TEXT] file=%s — scanned/image PDF (%d pages)", path.name, result.page_count)
        return result  # return empty PDFExtract (caller checks .is_empty)

    # ── Build smart text: first page (title) + representative sampling ────────
    # Always include first page fully (covers title/header)
    # For long documents, sample later pages to stay within max_chars
    combined_parts: list[str] = []
    budget = max_chars

    # First page always first
    if page_texts:
        first = page_texts[0][:budget]
        combined_parts.append(f"[صفحة 1]\n{first}")
        budget -= len(first)

    # Remaining pages: sample up to 3 more, each taking up to budget/3 chars
    per_page_budget = max(400, budget // 3) if budget > 0 else 0
    for i, pt in enumerate(page_texts[1:4], start=2):
        if budget <= 0:
            break
        snippet = pt[:per_page_budget]
        combined_parts.append(f"[صفحة {i}]\n{snippet}")
        budget -= len(snippet)

    result.full_text = "\n\n".join(combined_parts)

    # ── Extract first meaningful lines (title detection) ──────────────────────
    all_lines = page_texts[0].splitlines() if page_texts else []
    meaningful = [ln.strip() for ln in all_lines if len(ln.strip()) > 3][:8]
    result.first_lines = "\n".join(meaningful)

    # ── Detected keywords summary ─────────────────────────────────────────────
    keywords: list[str] = []
    if result.has_objectives:   keywords.append("أهداف تعلم")
    if result.has_questions:    keywords.append("أسئلة/اختبار")
    if result.has_grades_table: keywords.append("جدول درجات")
    if result.has_tables:       keywords.append("جداول")
    if result.has_ministry_header: keywords.append("ترويسة وزارية")
    result.detected_keywords = keywords

    logger.info(
        "[PDF TEXT EXTRACTED] file=%s pages=%d/%d chars=%d signals=%s",
        path.name, result.pages_with_text, result.page_count,
        len(result.full_text), keywords or "none",
    )
    return result


def extract_pdf_text(file_path: str | Path, max_chars: int = 3500) -> str | None:
    """
    Simple wrapper around extract_pdf_smart() that returns just the text string.
    Kept for backward compatibility. Returns None for empty/unreadable PDFs.
    """
    result = extract_pdf_smart(file_path, max_chars=max_chars)
    if result is None or result.is_empty:
        return None
    return result.full_text


def generate_pdf_preview(pdf_path: str | Path, max_pixels: int = 800) -> str | None:
    """
    Render the first page of a PDF as a JPEG image and return its path.

    Uses PyMuPDF (fitz) for reliable rendering. Falls back gracefully if:
    - pymupdf is not installed
    - PDF is encrypted / corrupt
    - Rendering fails for any reason

    The preview is saved as ``<pdf_path>_preview.jpg`` alongside the original.
    Returns the preview path on success, None on failure.
    """
    pdf_path = Path(pdf_path)
    preview_path = Path(str(pdf_path) + "_preview.jpg")

    # Return cached preview if it already exists and is non-empty
    if preview_path.exists() and preview_path.stat().st_size > 0:
        return str(preview_path)

    if not pdf_path.exists():
        logger.warning("[PDF PREVIEW] source not found: %s", pdf_path)
        return None

    try:
        import fitz  # pymupdf

        doc = fitz.open(str(pdf_path))
        if not doc.page_count:
            doc.close()
            return None

        page = doc[0]
        # Scale so the longer dimension is ≤ max_pixels
        rect  = page.rect
        scale = min(max_pixels / max(rect.width, rect.height), 2.0)
        mat   = fitz.Matrix(scale, scale)
        pix   = page.get_pixmap(matrix=mat, alpha=False)
        pix.save(str(preview_path))
        doc.close()

        logger.info(
            "[PDF PREVIEW] generated %s (%.0fx%.0f)",
            preview_path.name, pix.width, pix.height,
        )
        return str(preview_path)

    except ImportError:
        logger.debug("[PDF PREVIEW] pymupdf not installed — skipping preview generation")
        return None
    except Exception as exc:
        logger.warning("[PDF PREVIEW] failed for %s: %s", pdf_path.name, exc)
        return None


def storage_path_to_file_url(storage_path: str | None, base_url: str) -> str | None:
    """
    Convert a local storage_path to a public /files/ URL.

    storage_path is typically an absolute path like:
        /app/storage/teachers/123/evidences/file_abc.pdf
    The /files/ static mount serves the storage root, so we strip
    everything before 'teachers/' and prepend the base URL + /files/.
    """
    if not storage_path:
        return None
    try:
        parts = Path(storage_path).parts
        idx   = next(i for i, p in enumerate(parts) if p == "teachers")
        rel   = "/".join(parts[idx:])
        return f"{base_url.rstrip('/')}/files/{rel}"
    except StopIteration:
        logger.debug("[FILE URL] 'teachers' not in storage_path: %s", storage_path)
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
