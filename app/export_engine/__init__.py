"""
export_engine
─────────────
Phase-1 boundary for the portfolio export pipeline.

Responsibilities:
    payload_builder  →  produces a structured ``ExportPayload``
                        from ORM rows and current exporter helpers.
    renderer         →  renders the payload through the
                        ``templates/exports/<theme>`` family.
    media_resolver   →  isolates URL/preview/thumbnail decisions.
    pagination       →  translates importance scores into layout modes.
    schemas          →  pure DTOs (no ORM, no DB).

In Phase 1 the engine is a thin façade: it reuses the existing
``app.services.exporter`` helpers internally so behaviour is unchanged.
Subsequent phases will move classification / media / pagination logic
out of ``exporter.py`` and HTML, then drop the legacy compatibility
bridge that ``renderer`` currently exposes.

Ministry-of-Education colours, layout, and identity must live under
``app/templates/exports/<theme>`` only — never inside this package.
"""

from app.export_engine.schemas import (
    ExportCover,
    ExportItem,
    ExportMedia,
    ExportPayload,
    ExportSchool,
    ExportSection,
    ExportSummary,
    ExportTeacher,
)
from app.export_engine.payload_builder import build_export_payload
from app.export_engine.renderer import render_template

__all__ = [
    "ExportCover",
    "ExportItem",
    "ExportMedia",
    "ExportPayload",
    "ExportSchool",
    "ExportSection",
    "ExportSummary",
    "ExportTeacher",
    "build_export_payload",
    "render_template",
]
