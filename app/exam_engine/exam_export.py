"""
exam_engine.exam_export — independent PDF path for exam sheets.

Phase-10 contract
=================
This module *never* reuses the shawahid PDF generator
(``services.exporter._generate_pdf``). Exams render through their own
function so changes here can never destabilise the evidence-PDF
pipeline.

Playwright is the preferred backend, but it's optional — when it
isn't available the function returns the rendered HTML unchanged so
the caller can save it to disk and convert it externally.

The signature returns ``ExamExportResult`` so callers see exactly
which backend produced the output.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app.exam_engine.exam_renderer import render_exam_html
from app.exam_engine.exam_template import DEFAULT_TEMPLATE_NAME
from app.exam_engine.schemas import GeneratedExam

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExamExportResult:
    """The output of ``export_exam_pdf``.

    Either ``pdf_bytes`` is set (Playwright path) or ``html`` is set
    (fallback). The webhook decides what to do with each.
    """

    backend: str            # "playwright" / "html_only"
    html: str
    pdf_bytes: bytes | None = None
    notes: str = ""


def export_exam_pdf(
    exam: GeneratedExam,
    *,
    template_name: str = DEFAULT_TEMPLATE_NAME,
    use_playwright: bool = True,
) -> ExamExportResult:
    """Render and (optionally) print the exam to PDF.

    The Playwright import is *deferred* so the entire exam_engine
    package keeps loading on environments without Playwright. When
    Playwright fails or isn't available we return ``html_only`` and
    the caller decides next steps.
    """
    html = render_exam_html(exam, template_name=template_name)

    if not use_playwright:
        return ExamExportResult(
            backend="html_only",
            html=html,
            notes="use_playwright=False — returned HTML only.",
        )

    try:
        # Imported lazily to avoid forcing Playwright on every consumer.
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError as exc:
        logger.info("[EXAM EXPORT] Playwright not installed: %s", exc)
        return ExamExportResult(
            backend="html_only",
            html=html,
            notes=f"playwright_unavailable: {exc}",
        )

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context()
            page = context.new_page()
            page.set_content(html, wait_until="load")
            pdf_bytes = page.pdf(
                format="A4",
                print_background=True,
                margin={"top": "18mm", "bottom": "18mm",
                        "left": "16mm", "right": "16mm"},
            )
            browser.close()
            return ExamExportResult(
                backend="playwright",
                html=html,
                pdf_bytes=pdf_bytes,
                notes="ok",
            )
    except Exception as exc:  # network / sandbox failures
        logger.warning("[EXAM EXPORT] Playwright failed: %s", exc)
        return ExamExportResult(
            backend="html_only",
            html=html,
            notes=f"playwright_failed: {exc}",
        )


__all__ = ["ExamExportResult", "export_exam_pdf"]
