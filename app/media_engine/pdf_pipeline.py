"""
PDF pipeline.

Wraps the existing ``generate_pdf_preview`` helper from
``app.services.storage`` so callers go through ``media_engine`` only.
Behaviour is intentionally unchanged in Phase 4 — this is the
boundary, not the rewrite.

Hard rules:
    • No ORM / DB.
    • No Playwright (the rasterisation uses PyMuPDF, kept inside
      ``app.services.storage``).
    • All callers (exporter, webhook, future review_engine) must
      reach PDF preview / page count through this module.
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.media_engine.image_pipeline import image_to_data_uri

logger = logging.getLogger(__name__)


def generate_preview(pdf_path: str | Path) -> str | None:
    """Return the path to a JPEG render of the first PDF page.

    Returns ``None`` when:
        * the file is missing,
        * PyMuPDF (``fitz``) is unavailable,
        * the PDF is encrypted / corrupt,
        * the renderer crashed for any reason.

    The wrapper exists so external code does **not** import directly
    from ``app.services.storage`` — that module also contains
    download / hashing logic the engine should not re-export.
    """
    # Lazy import keeps the module loadable in environments (and tests)
    # where pymupdf isn't installed.
    from app.services.storage import generate_pdf_preview as _impl

    return _impl(pdf_path)


def first_page_data_uri(pdf_path: str | Path) -> str | None:
    """Convenience: render the first page **and** encode it inline.

    Used by the exporter to embed a thumbnail directly inside the PDF
    portfolio without bundling an external file. Returns ``None`` on
    any failure — the caller falls back to the PDF placeholder card.
    """
    preview = generate_preview(pdf_path)
    if not preview:
        return None
    return image_to_data_uri(preview)


def page_count(pdf_path: str | Path) -> int:
    """Return the number of pages, or 0 if the file is unreadable.

    Phase 4 keeps this best-effort — callers must treat the result as
    advisory only (e.g. UI hint, not a security check).
    """
    path = Path(pdf_path)
    if not path.exists():
        return 0
    try:
        import fitz  # pymupdf
    except ImportError:
        return 0
    try:
        doc = fitz.open(str(path))
        try:
            return int(doc.page_count or 0)
        finally:
            doc.close()
    except Exception as exc:
        logger.warning("[PDF PAGECOUNT] failed for %s: %s", path.name, exc)
        return 0
