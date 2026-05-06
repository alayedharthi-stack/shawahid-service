"""
Download UX layer for teacher portfolio PDFs.

Why this exists
---------------
Previously the WhatsApp button pointed straight at a static URL like:

    https://...up.railway.app/files/teachers/123/exports/shawahid_xxx.pdf

That URL is served by ``StaticFiles`` (raw FileResponse, no headers, no
buffering hints). On iOS Safari / Chrome that often surfaces as:

  • A blank white tab while the PDF is being fetched.
  • The user reloads, thinking the link is broken.
  • Slow networks make the gap feel longer.

This router fixes that with a lightweight two-step flow:

  1. ``GET /d/{teacher_id}/{filename}``
     → Returns a small branded HTML loading page (spinner + Arabic copy).
       The page auto-redirects to step 2 after ~600ms (with a JS redirect
       and a ``<meta http-equiv="refresh">`` fallback). The user never
       sees a blank tab.

  2. ``GET /d/{teacher_id}/{filename}/pdf``
     → ``FileResponse`` for the actual PDF, with explicit
       ``Content-Type: application/pdf`` and
       ``Content-Disposition: inline`` (so iOS still uses its native
       PDF viewer, but the browser knows what to do immediately).

Security: filename is whitelisted to ``shawahid_teacher_*.pdf`` and the
path is resolved + validated to be inside the teacher's export folder
(no traversal). Teacher must exist.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/d", tags=["downloads"])

_templates = Jinja2Templates(directory="app/templates")

# Hard whitelist for the export filename pattern produced by exporter.py:
#   shawahid_teacher_<id>_<YYYYMMDD>_<HHMMSS>.pdf
_EXPORT_FILENAME_RE = re.compile(
    r"^shawahid_teacher_\d+_\d{8}_\d{6}\.pdf$"
)


def _resolve_export_path(teacher_id: int, filename: str) -> Path:
    """Resolve and validate that <filename> belongs to <teacher_id>.

    Raises 404 on any mismatch (missing folder, missing file, traversal,
    or filename that doesn't match the export naming convention).
    """
    if not _EXPORT_FILENAME_RE.match(filename):
        logger.warning(
            "[DOWNLOAD] rejected filename teacher_id=%d filename=%s reason=pattern",
            teacher_id, filename,
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ملف غير موجود")

    export_dir = settings.export_storage(teacher_id).resolve()
    candidate = (export_dir / filename).resolve()

    # Defense-in-depth: ensure the resolved path is still inside export_dir.
    try:
        candidate.relative_to(export_dir)
    except ValueError:
        logger.warning(
            "[DOWNLOAD] traversal attempt teacher_id=%d filename=%s",
            teacher_id, filename,
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ملف غير موجود")

    if not candidate.exists() or not candidate.is_file():
        logger.warning(
            "[DOWNLOAD] missing file teacher_id=%d filename=%s",
            teacher_id, filename,
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ملف غير موجود")

    return candidate


@router.get("/{teacher_id}/{filename}", response_class=HTMLResponse)
async def download_loading_page(teacher_id: int, filename: str, request: Request):
    """Branded loading page that redirects to the actual PDF.

    The user sees the spinner instantly (no blank tab) while the browser
    starts streaming the PDF in the background.
    """
    # Validate up-front so we 404 here rather than on the redirect target.
    _resolve_export_path(teacher_id, filename)

    pdf_url = f"{settings.effective_base_url}/d/{teacher_id}/{filename}/pdf"

    logger.info(
        "[DOWNLOAD LOADING PAGE] teacher_id=%d filename=%s ua=%s",
        teacher_id, filename,
        request.headers.get("user-agent", "")[:80],
    )

    return _templates.TemplateResponse(
        "download_loading.html",
        {"request": request, "pdf_url": pdf_url},
    )


@router.get("/{teacher_id}/{filename}/pdf")
async def download_pdf(teacher_id: int, filename: str):
    """Serve the actual PDF with proper Content-Type and inline disposition.

    Inline (not attachment) so iOS / Android open the file in the native
    PDF viewer instead of forcing a download into Files. Desktop browsers
    still preview it inline. The explicit headers also stop browsers from
    showing a blank tab while sniffing the content type.
    """
    path = _resolve_export_path(teacher_id, filename)

    logger.info(
        "[DOWNLOAD PDF SERVED] teacher_id=%d filename=%s size=%d",
        teacher_id, filename, path.stat().st_size,
    )

    return FileResponse(
        str(path),
        media_type="application/pdf",
        filename=filename,
        headers={
            # Inline so the browser opens the PDF immediately (no blank tab).
            "Content-Disposition": f'inline; filename="{filename}"',
            # Cache the immutable, timestamped filename for a week.
            "Cache-Control": "public, max-age=604800, immutable",
            # Help proxies / scanners stop blocking the response while sniffing.
            "X-Content-Type-Options": "nosniff",
        },
    )
