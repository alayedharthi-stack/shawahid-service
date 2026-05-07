"""
review_engine — teacher-facing review layer.

Phase-5 contract
================
This package builds, validates, and mutates review data without
touching the PDF export machinery, templates, or Playwright.

Hard rules (enforced by the Phase-5 test suite):
    • No Playwright imports.
    • No ``app.export_engine`` imports.
    • ``review_service`` and pure modules (schemas, summary, links,
      permissions) contain no SQLAlchemy imports.
    • ``review_actions`` *may* use the ORM (it is the data-access
      boundary for write operations).

Public API
==========
"""
from __future__ import annotations

from app.review_engine.review_actions import (
    approve_evidence,
    delete_evidence,
    mark_duplicate,
    restore_evidence,
    toggle_exclude,
    update_evidence_category,
    update_evidence_title,
)
from app.review_engine.review_links import (
    generate_review_link,
    generate_review_token,
    validate_review_token,
)
from app.review_engine.review_permissions import (
    can_delete,
    can_edit,
    can_export,
    can_restore,
    can_review,
)
from app.review_engine.review_service import build_review_session
from app.review_engine.review_summary import (
    build_categories_line,
    build_export_readiness,
    build_summary_text,
)
from app.review_engine.schemas import (
    IMPORTANCE_MEDIUM,
    IMPORTANCE_SIMPLE,
    IMPORTANCE_STRONG,
    LOW_CONFIDENCE_THRESHOLD,
    ReviewItem,
    ReviewSession,
)

__all__ = [
    # DTOs
    "ReviewItem",
    "ReviewSession",
    # constants
    "IMPORTANCE_STRONG", "IMPORTANCE_MEDIUM", "IMPORTANCE_SIMPLE",
    "LOW_CONFIDENCE_THRESHOLD",
    # service
    "build_review_session",
    # actions
    "approve_evidence", "delete_evidence", "mark_duplicate",
    "restore_evidence", "toggle_exclude",
    "update_evidence_category", "update_evidence_title",
    # links
    "generate_review_token", "generate_review_link", "validate_review_token",
    # summary
    "build_summary_text", "build_categories_line", "build_export_readiness",
    # permissions
    "can_review", "can_export", "can_delete", "can_restore", "can_edit",
]
