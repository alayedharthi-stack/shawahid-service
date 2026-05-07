"""
review_service — builds a ReviewSession from raw evidence data.

Hard rules (enforced by Phase-5 test suite):
    • No SQLAlchemy / ORM imports here — DB access stays in api/review.py.
    • No Playwright, no export_engine.
    • Inputs are plain attribute-accessible objects (ORM rows OR SimpleNamespace).
    • Always check content_hash for duplicate detection.
    • Always route preview URLs through media_engine.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime

from app.media_engine.media_urls import build_media_urls
from app.review_engine.schemas import (
    LOW_CONFIDENCE_THRESHOLD,
    IMPORTANCE_MEDIUM,
    IMPORTANCE_SIMPLE,
    IMPORTANCE_STRONG,
    ReviewItem,
    ReviewSession,
)

logger = logging.getLogger(__name__)


def _safe_str(value) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    return "" if s.lower() in {"null", "none", "undefined"} else s


def _confidence_from_evidence(ev) -> float | None:
    """Extract confidence score from ``ai_raw`` JSON blob, if present."""
    ai_raw = getattr(ev, "ai_raw", None)
    if isinstance(ai_raw, dict):
        raw = ai_raw.get("confidence_score")
        if raw is not None:
            try:
                return float(raw)
            except (TypeError, ValueError):
                pass
    return None


def _importance_from_evidence(ev) -> str:
    """Best-effort importance from ai_raw, then from category/type heuristics."""
    ai_raw = getattr(ev, "ai_raw", None)
    if isinstance(ai_raw, dict):
        raw = ai_raw.get("importance_score", "")
        normalised = str(raw).lower()
        if normalised in (IMPORTANCE_STRONG, "قوي", "strong"):
            return IMPORTANCE_STRONG
        if normalised in (IMPORTANCE_MEDIUM, "متوسط", "medium"):
            return IMPORTANCE_MEDIUM
        if normalised in (IMPORTANCE_SIMPLE, "بسيط", "simple"):
            return IMPORTANCE_SIMPLE
    # Heuristic: PDFs and voice notes without enrichment are medium at best.
    ev_type = _safe_str(getattr(ev, "evidence_type", ""))
    if ev_type in ("video", "image"):
        return IMPORTANCE_MEDIUM
    return IMPORTANCE_SIMPLE


def _preview_for_evidence(ev, base_url: str) -> str | None:
    """Delegate preview URL resolution to media_engine — never build inline."""
    try:
        urls = build_media_urls(
            evidence_id=getattr(ev, "id", None),
            evidence_type=getattr(ev, "evidence_type", None),
            storage_path=getattr(ev, "storage_path", None),
            media_url=getattr(ev, "media_url", None),
            base_url=base_url,
        )
        return urls.preview_url or urls.thumbnail_url or urls.public_url
    except Exception as exc:
        logger.warning(
            "[REVIEW] preview resolution failed for evidence %s: %s",
            getattr(ev, "id", "?"), exc,
        )
        return None


def _format_date(dt) -> str:
    if dt is None:
        return ""
    if isinstance(dt, datetime):
        return dt.strftime("%Y/%m/%d")
    try:
        return str(dt)[:10]
    except Exception:
        return ""


def _detect_duplicates(evidences) -> set[int]:
    """Return the set of evidence IDs that share a non-null content_hash
    with at least one other evidence in the same teacher's collection."""
    hash_to_ids: dict[str, list[int]] = {}
    for ev in evidences:
        h = _safe_str(getattr(ev, "content_hash", None))
        if h:
            hash_to_ids.setdefault(h, []).append(ev.id)
    duplicate_ids: set[int] = set()
    for ids in hash_to_ids.values():
        if len(ids) > 1:
            duplicate_ids.update(ids)
    return duplicate_ids


def build_review_session(
    evidences,
    *,
    teacher_id: int,
    teacher_name: str | None = None,
    base_url: str = "",
) -> ReviewSession:
    """Build a :class:`ReviewSession` from a flat list of evidence rows.

    Parameters
    ----------
    evidences:
        Any iterable of attribute-accessible objects (ORM rows or
        ``SimpleNamespace``). Must have at minimum: ``id``,
        ``evidence_type``, ``category``, ``title``,
        ``is_excluded_from_export``, ``content_hash``.
    teacher_id:
        DB primary key — stored as-is in the session.
    teacher_name:
        Display name for the review page header.
    base_url:
        Service base URL used by media_engine to build preview URLs.
    """
    evidence_list = list(evidences)
    duplicate_ids = _detect_duplicates(evidence_list)

    items: list[ReviewItem] = []
    for ev in evidence_list:
        ev_type   = _safe_str(getattr(ev, "evidence_type", "")) or "text"
        category  = _safe_str(getattr(ev, "category", "")) or "أخرى"
        title     = _safe_str(getattr(ev, "title", "")) or "بدون عنوان"
        is_excl   = bool(getattr(ev, "is_excluded_from_export", False))
        conf      = _confidence_from_evidence(ev)
        imp       = _importance_from_evidence(ev)
        needs_rev = (conf is not None and conf < LOW_CONFIDENCE_THRESHOLD)

        preview: str | None = None
        if base_url and ev_type in ("image", "video"):
            preview = _preview_for_evidence(ev, base_url)

        msg_text = _safe_str(getattr(ev, "message_text", ""))
        if len(msg_text) > 200:
            msg_text = msg_text[:197] + "…"

        items.append(ReviewItem(
            evidence_id=ev.id,
            title=title,
            category=category,
            subcategory=_safe_str(getattr(ev, "grade", "")) or _safe_str(getattr(ev, "subject", "")),
            importance_score=imp,
            confidence_score=conf,
            media_type=ev_type,
            preview_url=preview,
            needs_review=needs_rev,
            is_duplicate=(ev.id in duplicate_ids),
            is_excluded=is_excl,
            created_at=_format_date(getattr(ev, "created_at", None)),
            file_name=_safe_str(getattr(ev, "file_name", "")) or None,
            message_text=msg_text or None,
        ))

    # Active = not excluded
    active = [it for it in items if not it.is_excluded]
    excluded = [it for it in items if it.is_excluded]
    ordered = active + excluded   # active items appear first in the review list

    cat_counter: Counter[str] = Counter(it.category for it in active)
    dup_count  = sum(1 for it in items if it.is_duplicate and not it.is_excluded)
    low_conf   = sum(1 for it in active if it.needs_review)
    strong     = sum(1 for it in active if it.importance_score == IMPORTANCE_STRONG)

    return ReviewSession(
        teacher_id=teacher_id,
        teacher_name=teacher_name,
        total_items=len(items),
        active_items=len(active),
        categories_summary=dict(cat_counter.most_common()),
        duplicates_count=dup_count,
        low_confidence_count=low_conf,
        strong_count=strong,
        items=ordered,
    )
