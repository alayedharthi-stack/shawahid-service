"""
services.storage — thin compatibility adapter on top of ``storage_engine``.

Phase-7 contract
================
Every call here delegates to ``storage_engine``. Existing callers
(webhook, exporter, api.media, media_engine.pdf_pipeline) keep their
imports unchanged so this phase ships without touching the call sites.

PDF text extraction (``extract_pdf_*`` and ``generate_pdf_preview``)
remains here because it is *not* a pure storage concern — it lives in
the same legacy module so as not to expand storage_engine's scope.

The adapter exposes the historical public surface:

    download_and_save(...)           ← returns (storage_path, safe_filename, content_hash)
    extract_urls(text)
    detect_evidence_type(...)
    extract_pdf_text(...) / extract_pdf_smart(...)
    generate_pdf_preview(...)
    storage_path_to_file_url(...)
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from app.core.config import settings
from app.storage_engine.file_store import (
    download_and_save as _engine_download_and_save,
)
from app.storage_engine.paths import safe_filename as _engine_safe_filename

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# URL helpers
# ──────────────────────────────────────────────────────────────────────────────


_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


def extract_urls(text: str | None) -> list[str]:
    """Return all HTTP/HTTPS URLs found in text."""
    if not text:
        return []
    return _URL_RE.findall(text)


# ──────────────────────────────────────────────────────────────────────────────
# Filename helper (legacy alias)
# ──────────────────────────────────────────────────────────────────────────────


def _safe_filename(original: str | None, mime_type: str | None) -> str:
    """Legacy alias retained for any external callers — delegates to
    :func:`storage_engine.paths.safe_filename`.
    """
    return _engine_safe_filename(original, mime_type)


# ──────────────────────────────────────────────────────────────────────────────
# download_and_save — legacy 3-tuple contract
# ──────────────────────────────────────────────────────────────────────────────


async def download_and_save(
    teacher_id: int,
    media_url: str,
    original_filename: str | None = None,
    mime_type: str | None = None,
    auth_token: str | None = None,
) -> tuple[str, str, str]:
    """Backwards-compatible wrapper around
    :func:`storage_engine.download_and_save`.

    Preserves the historical return shape ``(storage_path, safe_filename,
    content_hash)`` so the WhatsApp webhook keeps working unchanged.
    """
    try:
        stored = await _engine_download_and_save(
            teacher_id=teacher_id,
            media_url=media_url,
            original_filename=original_filename,
            mime_type=mime_type,
            auth_token=auth_token,
            use_legacy_layout=True,
        )
    except Exception as exc:
        logger.error(
            "Failed to download media for teacher %d from %s: %s",
            teacher_id, media_url, exc,
        )
        raise

    storage_path = stored.stored_path
    safe_name = Path(storage_path).name
    return storage_path, safe_name, stored.content_hash


# ──────────────────────────────────────────────────────────────────────────────
# Evidence type detection (pure helper, kept here for backwards compat)
# ──────────────────────────────────────────────────────────────────────────────


def detect_evidence_type(
    mime_type: str | None,
    file_name: str | None,
    text: str | None,
) -> str:
    """Map (mime_type, file_name, text) → an evidence_type label."""
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
    if mt in (
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ):
        return "document"
    if fn.endswith((".doc", ".docx")):
        return "document"
    return "document"


# ──────────────────────────────────────────────────────────────────────────────
# PDF text extraction (kept in this module — not a storage_engine concern)
# ──────────────────────────────────────────────────────────────────────────────


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
        self.first_lines: str = ""
        self.has_tables: bool = False
        self.has_questions: bool = False
        self.has_objectives: bool = False
        self.has_grades_table: bool = False
        self.has_ministry_header: bool = False
        self.detected_keywords: list[str] = []

    @property
    def is_empty(self) -> bool:
        return not self.full_text.strip()


def extract_pdf_smart(file_path: str | Path, max_chars: int = 3500) -> PDFExtract | None:
    """Smart multi-page PDF extraction (objective/question/grade signals).

    Returns ``None`` when ``pdfplumber`` is unavailable or the PDF is
    unreadable. Logs:
    ``[PDF TEXT EXTRACTED]`` / ``[PDF NO TEXT]`` / ``[PDF EXTRACT ERROR]``.
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

    _OBJECTIVES_KW = ("هدف", "هدفًا", "نواتج التعلم", "بنهاية الدرس", "الأهداف",
                      "يستطيع الطالب", "يتمكن المتعلم")
    _QUESTIONS_KW  = ("السؤال", "اختر", "أجب", "صح أم خطأ", "اختيار من متعدد",
                      "الدرجة", "نقطة", "علامة")
    _GRADES_KW     = ("الدرجة", "مجموع", "كشف", "رصد", "التقدير", "ممتاز",
                      "جيد جدًا", "جيد", "مقبول", "راسب")
    _MINISTRY_KW   = ("وزارة التعليم", "المملكة العربية السعودية", "إدارة التعليم",
                      "مدير المدرسة", "الرقم الوزاري")

    try:
        with pdfplumber.open(str(path)) as pdf:
            result.page_count = len(pdf.pages)
            page_texts: list[str] = []

            for i, page in enumerate(pdf.pages):
                try:
                    if page.extract_tables():
                        result.has_tables = True
                except Exception:
                    pass

                page_text = (page.extract_text() or "").strip()
                if page_text:
                    result.pages_with_text += 1
                    page_texts.append(page_text)
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
        logger.info("[PDF NO TEXT] file=%s — scanned/image PDF (%d pages)",
                    path.name, result.page_count)
        return result

    combined_parts: list[str] = []
    budget = max_chars
    if page_texts:
        first = page_texts[0][:budget]
        combined_parts.append(f"[صفحة 1]\n{first}")
        budget -= len(first)
    per_page_budget = max(400, budget // 3) if budget > 0 else 0
    for i, pt in enumerate(page_texts[1:4], start=2):
        if budget <= 0:
            break
        snippet = pt[:per_page_budget]
        combined_parts.append(f"[صفحة {i}]\n{snippet}")
        budget -= len(snippet)
    result.full_text = "\n\n".join(combined_parts)

    all_lines = page_texts[0].splitlines() if page_texts else []
    meaningful = [ln.strip() for ln in all_lines if len(ln.strip()) > 3][:8]
    result.first_lines = "\n".join(meaningful)

    keywords: list[str] = []
    if result.has_objectives:      keywords.append("أهداف تعلم")
    if result.has_questions:       keywords.append("أسئلة/اختبار")
    if result.has_grades_table:    keywords.append("جدول درجات")
    if result.has_tables:          keywords.append("جداول")
    if result.has_ministry_header: keywords.append("ترويسة وزارية")
    result.detected_keywords = keywords

    logger.info(
        "[PDF TEXT EXTRACTED] file=%s pages=%d/%d chars=%d signals=%s",
        path.name, result.pages_with_text, result.page_count,
        len(result.full_text), keywords or "none",
    )
    return result


def extract_pdf_text(file_path: str | Path, max_chars: int = 3500) -> str | None:
    """Simple wrapper that returns just the extracted text string."""
    result = extract_pdf_smart(file_path, max_chars=max_chars)
    if result is None or result.is_empty:
        return None
    return result.full_text


def generate_pdf_preview(pdf_path: str | Path, max_pixels: int = 800) -> str | None:
    """Render the first page of a PDF as a JPEG preview alongside the file.

    Returns the preview path on success, ``None`` on failure.
    """
    pdf_path = Path(pdf_path)
    preview_path = Path(str(pdf_path) + "_preview.jpg")

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
        rect = page.rect
        scale = min(max_pixels / max(rect.width, rect.height), 2.0)
        mat = fitz.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=mat, alpha=False)
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


# ──────────────────────────────────────────────────────────────────────────────
# Public URL helper (delegates to media_engine, kept for legacy imports)
# ──────────────────────────────────────────────────────────────────────────────


def storage_path_to_file_url(storage_path: str | None, base_url: str) -> str | None:
    """Phase-4 adapter — delegates to ``media_engine.media_urls``."""
    from app.media_engine.media_urls import storage_path_to_public_url
    return storage_path_to_public_url(storage_path, base_url)
