"""
ExportPayload DTOs.

Hard rules:
    • No SQLAlchemy / ORM imports.
    • No DB sessions.
    • No file I/O.
    • No Jinja, no CSS, no Playwright.

The renderer consumes these objects only — never the ORM. The builder
is allowed to reuse helper functions from ``app.services.exporter``
during Phase 1 so behaviour matches the legacy pipeline exactly, but
the resulting payload itself is a pure data structure.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ── Smart layout vocabulary ──────────────────────────────────────────────
# Importance score for a single evidence:
#   strong  → wide hero card with the full description.
#   medium  → standard evidence card.
#   weak    → compact card with title + media link only.
IMPORTANCE_STRONG = "strong"
IMPORTANCE_MEDIUM = "medium"
IMPORTANCE_WEAK = "weak"

# Layout mode for a single evidence card.
LAYOUT_HERO = "hero"
LAYOUT_NORMAL = "normal"
LAYOUT_COMPACT = "compact"

# Layout mode for a whole section.
SECTION_LAYOUT_DEFAULT = "default"
SECTION_LAYOUT_COMPACT_GRID = "compact_grid"


@dataclass
class ExportMedia:
    """Pre-resolved URLs the renderer should use as-is.

    The builder is responsible for picking the right URL per evidence
    type (image data URI, PDF preview, video thumbnail, audio button,
    etc.). The template never makes that decision.
    """

    file_url: str | None = None
    preview_url: str | None = None
    thumbnail_url: str | None = None
    player_url: str | None = None
    fallback_url: str | None = None


@dataclass
class ExportItem:
    """A single evidence as the renderer will see it."""

    id: int | None = None
    title: str = ""
    description: str = ""
    evidence_type: str = ""
    media_type: str = ""
    category: str = ""
    subcategory: str = ""
    ministry_standard: str = ""
    objective: str = ""
    student_impact: str = ""
    teacher_reflection: str = ""
    importance_score: str = IMPORTANCE_MEDIUM
    layout_mode: str = LAYOUT_NORMAL
    media: ExportMedia = field(default_factory=ExportMedia)
    # Phase-1 bridge: full normalised dict from the legacy exporter so
    # the existing template can keep rendering without changes. Phase 2
    # removes this and the template reads from typed fields only.
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExportSection:
    """A category section in the portfolio (e.g. "التخطيط")."""

    key: str = ""
    title: str = ""
    order: int = 0
    description: str = ""
    items: list[ExportItem] = field(default_factory=list)
    layout_mode: str = SECTION_LAYOUT_DEFAULT
    # Foundational sections (planning, follow-up log) render BEFORE the
    # statistics / TOC pages. Everything else renders after. Phase 2:
    # the template uses this flag instead of reading the legacy split
    # from `payload.legacy_context`.
    is_leading: bool = False
    # Phase-1 bridge: the legacy category dict (with `name`, `desc`,
    # `value`, `evidences`, `count`, `is_admin_grid`, etc.).
    # TODO(phase-3): remove this once every legacy field has a typed
    # equivalent on ExportItem / ExportSection.
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def count(self) -> int:
        return len(self.items)


@dataclass
class ExportTeacher:
    id: int | None = None
    name: str = ""
    subject: str = ""
    stage: str = ""
    grades: str = ""


@dataclass
class ExportSchool:
    name: str = ""
    principal_name: str = ""


@dataclass
class ExportSummary:
    total_count: int = 0
    image_count: int = 0
    video_count: int = 0
    audio_count: int = 0
    file_count: int = 0
    url_count: int = 0
    text_count: int = 0
    top_categories: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ExportCover:
    title: str = "ملف الشواهد"
    subtitle: str = ""
    academic_year: str = ""
    generated_at: str = ""


@dataclass
class ExportPayload:
    """The single object every export theme renders against.

    Layers above (renderer + templates) MUST treat this as read-only
    and MUST NOT touch ORM or DB to fetch additional data.
    """

    teacher: ExportTeacher
    school: ExportSchool
    export_mode: str = "full"
    generated_at: str = ""
    cover: ExportCover = field(default_factory=ExportCover)
    summary: ExportSummary = field(default_factory=ExportSummary)
    sections: list[ExportSection] = field(default_factory=list)

    # Phase-1 bridge: extra context the legacy template needs (logo
    # data URI, stats dict, performance analysis, leading/remaining
    # categories split). Phase 2 removes this and exposes typed fields.
    legacy_context: dict[str, Any] = field(default_factory=dict)
