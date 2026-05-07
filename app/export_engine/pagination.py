"""
Smart layout helpers.

Translates importance scoring + section flags into the layout vocabulary
the renderer understands (``hero`` / ``normal`` / ``compact`` per item,
``default`` / ``compact_grid`` per section).

Phase 1 keeps the same effective rules the legacy template already
enforces — nothing changes visually. Phase 2 will introduce real
page-height aware pagination.
"""
from __future__ import annotations

from app.export_engine.schemas import (
    LAYOUT_COMPACT,
    LAYOUT_HERO,
    LAYOUT_NORMAL,
    SECTION_LAYOUT_COMPACT_GRID,
    SECTION_LAYOUT_DEFAULT,
)


def resolve_item_layout_mode(
    *,
    is_admin_section: bool,
    importance: str,
    is_compact: bool,
) -> str:
    """Decide which card variant the renderer should use for a single item."""
    if is_admin_section or is_compact:
        return LAYOUT_COMPACT
    if importance == "strong":
        return LAYOUT_HERO
    return LAYOUT_NORMAL


def resolve_section_layout_mode(*, is_admin_grid: bool) -> str:
    """Decide which container layout the renderer should use for a section."""
    if is_admin_grid:
        return SECTION_LAYOUT_COMPACT_GRID
    return SECTION_LAYOUT_DEFAULT
