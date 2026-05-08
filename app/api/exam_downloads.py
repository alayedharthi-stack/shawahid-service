"""
Exam-PDF download endpoints — independent from the portfolio downloads.

Layout
------
``exam_flow._render_pdf_safely`` writes one of:

    storage/teachers/{teacher_id}/exams/{exam_id}.pdf       (Playwright)
    storage/teachers/{teacher_id}/exams/{exam_id}.html      (fallback)

This router exposes a single, mobile-friendly download URL:

    GET /exams/download/{teacher_id}/{exam_id}

* If a ``.pdf`` exists → ``FileResponse`` with ``application/pdf`` and
  ``Content-Disposition: inline`` so iOS / Android open the native PDF
  viewer instead of forcing a save.
* If only the ``.html`` is present (no Playwright) → ``HTMLResponse``
  so the teacher can still print or screenshot from their browser.
* Otherwise → 404 with a clear Arabic message.

Security
--------
``exam_id`` is whitelisted to the format produced by
:func:`app.exam_engine.schemas.GeneratedExam.exam_id` (``ex-`` + 10 hex
chars). The resolved path is validated to live inside the teacher's own
exam folder — no traversal is possible.

This router is intentionally independent from
:mod:`app.api.downloads` (which serves portfolio PDFs) so changes to
either side cannot destabilise the other.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse

from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/exams", tags=["exam-downloads"])

# GeneratedExam.exam_id is built as ``f"ex-{uuid.uuid4().hex[:10]}"``,
# so 10 hex chars is the canonical shape. Allow up to 32 to stay safe
# if the format ever widens, but never accept slashes or dots.
_EXAM_ID_RE = re.compile(r"^ex-[a-f0-9]{6,32}$")


def _resolve_exam_files(teacher_id: int, exam_id: str) -> tuple[Path | None, Path | None]:
    """Return ``(pdf_path, html_path)`` — either may be ``None``.

    Validates the inputs and ensures both candidate paths live inside
    the teacher's own exam directory (defence-in-depth against path
    traversal even though the regex already forbids slashes).
    """
    if not _EXAM_ID_RE.match(exam_id):
        logger.warning(
            "[EXAM DOWNLOAD] rejected exam_id teacher_id=%d exam_id=%s reason=pattern",
            teacher_id, exam_id,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="رابط الاختبار غير صالح",
        )

    exams_dir = (settings.teacher_storage(teacher_id) / "exams").resolve()
    pdf_candidate = (exams_dir / f"{exam_id}.pdf").resolve()
    html_candidate = (exams_dir / f"{exam_id}.html").resolve()

    pdf_path = pdf_candidate if pdf_candidate.is_file() else None
    html_path = html_candidate if html_candidate.is_file() else None

    # Defence-in-depth: never serve anything outside the teacher's folder.
    for cand in (pdf_path, html_path):
        if cand is None:
            continue
        try:
            cand.relative_to(exams_dir)
        except ValueError:
            logger.warning(
                "[EXAM DOWNLOAD] traversal attempt teacher_id=%d exam_id=%s",
                teacher_id, exam_id,
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="رابط الاختبار غير صالح",
            )

    return pdf_path, html_path


def build_exam_download_url(*, teacher_id: int, exam_id: str) -> str:
    """Public URL the WhatsApp button should open.

    Centralised so callers (webhook, tests) never need to format the
    URL by hand. Returned URL always uses ``settings.effective_base_url``.
    """
    base = settings.effective_base_url.rstrip("/")
    return f"{base}/exams/download/{teacher_id}/{exam_id}"


@router.get("/download/{teacher_id}/{exam_id}")
async def download_exam(teacher_id: int, exam_id: str):
    """Serve the exam — PDF preferred, HTML fallback, 404 otherwise."""
    pdf_path, html_path = _resolve_exam_files(teacher_id, exam_id)

    if pdf_path is not None:
        logger.info(
            "[EXAM DOWNLOAD PDF] teacher_id=%d exam_id=%s size=%dKB",
            teacher_id, exam_id, pdf_path.stat().st_size // 1024,
        )
        return FileResponse(
            str(pdf_path),
            media_type="application/pdf",
            filename=f"exam-{exam_id}.pdf",
            headers={
                "Content-Disposition": f'inline; filename="exam-{exam_id}.pdf"',
                "Cache-Control": "public, max-age=604800, immutable",
                "X-Content-Type-Options": "nosniff",
            },
        )

    if html_path is not None:
        logger.info(
            "[EXAM DOWNLOAD HTML] teacher_id=%d exam_id=%s size=%dKB",
            teacher_id, exam_id, html_path.stat().st_size // 1024,
        )
        return HTMLResponse(
            content=html_path.read_text(encoding="utf-8"),
            headers={
                # Print-friendly fallback when Playwright isn't available.
                "Content-Disposition": f'inline; filename="exam-{exam_id}.html"',
                "Cache-Control": "public, max-age=604800",
                "X-Content-Type-Options": "nosniff",
            },
        )

    logger.warning(
        "[EXAM DOWNLOAD MISSING] teacher_id=%d exam_id=%s",
        teacher_id, exam_id,
    )
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="لم أجد ملف الاختبار. أعد إنشاء الاختبار وسأرسل الرابط من جديد 🌿",
    )


@router.get("/health/_self", response_class=PlainTextResponse)
async def exam_downloads_health() -> str:
    """Tiny static endpoint so Railway can probe the router cheaply."""
    return "ok"
