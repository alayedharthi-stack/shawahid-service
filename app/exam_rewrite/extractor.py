"""
exam_rewrite.extractor — multi-page PDF → raw text.

Differs from ``app.services.storage.extract_pdf_smart`` in two ways:
    1. We pull text for *every* page, not just the first four — exams
       routinely span 4-12 pages and Phase-3 wants the full content.
    2. We return raw page text WITHOUT signal flags. The cleaner and
       parser layers handle the rest.

Phase-3 hard rules:
    • No GPT, no DB, no network.
    • No PDF generation.
    • Pure dependency on ``pdfplumber`` (already in
      ``requirements.txt``). If pdfplumber is missing or the file is
      unreadable we return an empty list and let the orchestrator
      decide what to tell the teacher.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def extract_all_pages(file_path: str | Path) -> list[str]:
    """Return per-page text for the PDF at ``file_path``.

    ``result[i]`` is the text of page ``i+1`` (or ``""`` for blank
    pages). The list is empty when:
        • the file is missing,
        • ``pdfplumber`` isn't installed,
        • the PDF is encrypted / corrupt,
        • every page is image-only.
    """
    path = Path(file_path)
    if not path.exists():
        logger.warning("[EXAM EXTRACT] file not found: %s", path)
        return []

    try:
        import pdfplumber
    except ImportError:
        logger.warning("[EXAM EXTRACT] pdfplumber not installed")
        return []

    try:
        pages_text: list[str] = []
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages:
                try:
                    raw = page.extract_text() or ""
                except Exception as page_exc:
                    logger.debug(
                        "[EXAM EXTRACT] page extract failed in %s: %s",
                        path.name, page_exc,
                    )
                    raw = ""
                pages_text.append(raw.strip())
    except Exception as exc:
        logger.warning("[EXAM EXTRACT] failed for %s: %s", path.name, exc)
        return []

    if not any(pages_text):
        logger.info(
            "[EXAM EXTRACT] %s — no extractable text on %d pages (scanned?)",
            path.name, len(pages_text),
        )
        return []

    logger.info(
        "[EXAM EXTRACT] %s — pages=%d non_empty=%d chars=%d",
        path.name, len(pages_text),
        sum(1 for p in pages_text if p),
        sum(len(p) for p in pages_text),
    )
    return pages_text


def join_pages(pages: list[str]) -> str:
    """Concatenate page texts with explicit page markers.

    The page markers are kept in the joined text so downstream layers
    can drop boilerplate footers ("صفحة X من Y") confidently.
    """
    parts: list[str] = []
    for idx, page in enumerate(pages, start=1):
        if not page:
            continue
        parts.append(f"[صفحة {idx}]\n{page.strip()}")
    return "\n\n".join(parts)


__all__ = ["extract_all_pages", "join_pages"]
