"""
Review Engine DTOs — pure dataclasses, no ORM, no Playwright, no export_engine.

These objects are the contract between the DB layer (api/review.py)
and the review engine. Consumers read them as value objects.

Phase-5 contract:
    • review_engine must never import SQLAlchemy or export_engine.
    • DTOs are constructed from plain dicts / SimpleNamespace objects
      by review_service.build_review_session.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ── Importance vocabulary (mirrors media_engine / classification) ──────
IMPORTANCE_STRONG = "strong"
IMPORTANCE_MEDIUM = "medium"
IMPORTANCE_SIMPLE = "simple"

# Low-confidence threshold: items whose AI confidence is below this value
# are flagged ``needs_review = True`` so the teacher can verify them.
LOW_CONFIDENCE_THRESHOLD = 0.65


@dataclass
class ReviewItem:
    """A single evidence as the review page sees it.

    Source: a plain dict or ORM row read by api/review.py and normalised
    by ``review_service.build_review_session``. Never contains raw ORM
    references after the API boundary.

    Fields
    ------
    evidence_id        Stable row ID.
    title              Display title (may be AI-generated default).
    category           Primary category string.
    subcategory        Sub-category or empty string.
    importance_score   One of IMPORTANCE_STRONG / MEDIUM / SIMPLE.
    confidence_score   AI confidence 0.0–1.0.  ``None`` = not yet analysed.
    media_type         Evidence type: image/pdf/video/audio/text/url/…
    preview_url        Data URI or public /files/ URL for the thumbnail.
                       ``None`` if no visual representation is available.
    needs_review       True when confidence is low or category is uncertain.
    is_duplicate       True when another evidence shares the same content_hash.
    is_excluded        True when soft-deleted (excluded from export).
    created_at         ISO-like display string "YYYY/MM/DD".
    file_name          Original filename (or None).
    message_text       Short text excerpt (≤ 200 chars).
    """

    evidence_id: int
    title: str
    category: str
    subcategory: str = ""
    importance_score: str = IMPORTANCE_MEDIUM
    confidence_score: float | None = None
    media_type: str = "text"
    preview_url: str | None = None
    needs_review: bool = False
    is_duplicate: bool = False
    is_excluded: bool = False
    created_at: str = ""
    file_name: str | None = None
    message_text: str | None = None


@dataclass
class ReviewSession:
    """Aggregated review data for one teacher.

    Built by ``review_service.build_review_session``. The API layer
    renders this into HTML via Jinja — the engine itself never touches
    templates.

    Fields
    ------
    teacher_id          DB primary key.
    teacher_name        Display name (or None if not set).
    total_items         All evidences, including excluded ones.
    active_items        Evidences that are NOT excluded.
    categories_summary  {category: count} for active items only.
    duplicates_count    Items flagged is_duplicate.
    low_confidence_count Items flagged needs_review.
    strong_count        Items with importance_score == IMPORTANCE_STRONG.
    items               Full ordered list (active first, then excluded).
    """

    teacher_id: int
    teacher_name: str | None = None
    total_items: int = 0
    active_items: int = 0
    categories_summary: dict[str, int] = field(default_factory=dict)
    duplicates_count: int = 0
    low_confidence_count: int = 0
    strong_count: int = 0
    items: list[ReviewItem] = field(default_factory=list)
