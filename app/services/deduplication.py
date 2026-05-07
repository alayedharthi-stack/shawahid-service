"""
services.deduplication — Phase-7 thin adapter on ``storage_engine.dedup``
+ legacy difflib text-similarity helpers.

Responsibility split
====================
* **Hashing primitives** — ``hash_bytes``, ``hash_text``, ``hash_url``
  delegate to ``storage_engine.hashing``.
* **Hash-based dedup** — ``is_exact_duplicate`` / ``get_evidence_by_hash``
  delegate to ``storage_engine.dedup``.
* **Near-duplicate text** (difflib) — kept here, not a storage_engine
  concern.
* **Export-time dedup** (``deduplicate_for_export``) — kept here, used
  only by the export pipeline.

This module is therefore *backwards-compatible*: every existing caller
keeps working untouched.
"""
from __future__ import annotations

import difflib
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.evidence import Evidence
from app.storage_engine.dedup import (
    find_duplicate_by_hash as _engine_find_duplicate_by_hash,
)
from app.storage_engine.hashing import (
    _clean_text as _clean,
    hash_bytes,
    hash_text,
    hash_url,
)

logger = logging.getLogger(__name__)


# ── Thresholds (preserved from legacy module) ────────────────────────────────
TEXT_DUPLICATE_RATIO  = 0.90
TEXT_MIN_LENGTH       = 20
TEXT_LOOKBACK_DAYS    = 90
TEXT_LOOKBACK_LIMIT   = 60


# ──────────────────────────────────────────────────────────────────────────────
# Hash-based dedup — adapter
# ──────────────────────────────────────────────────────────────────────────────


def is_exact_duplicate(db: Session, teacher_id: int, content_hash: str) -> bool:
    """Return ``True`` when an evidence with the same content_hash exists
    for this teacher (delegates to ``storage_engine.dedup``).
    """
    return _engine_find_duplicate_by_hash(db, teacher_id, content_hash) is not None


def get_evidence_by_hash(
    db: Session,
    teacher_id: int,
    content_hash: str,
) -> Evidence | None:
    """Return the most recent matching Evidence row for this teacher."""
    return _engine_find_duplicate_by_hash(db, teacher_id, content_hash)


# ──────────────────────────────────────────────────────────────────────────────
# Near-duplicate text (kept here — not a storage_engine concern)
# ──────────────────────────────────────────────────────────────────────────────


def find_near_duplicate_text(
    db: Session,
    teacher_id: int,
    text: str,
) -> Evidence | None:
    """Return an existing text Evidence whose cleaned content is ≥
    ``TEXT_DUPLICATE_RATIO`` similar to ``text``.
    """
    if len(text.strip()) < TEXT_MIN_LENGTH:
        return None

    cutoff = datetime.now(timezone.utc) - timedelta(days=TEXT_LOOKBACK_DAYS)
    recent: list[Evidence] = (
        db.query(Evidence)
        .filter(
            Evidence.teacher_id    == teacher_id,
            Evidence.evidence_type == "text",
            Evidence.created_at    >= cutoff,
        )
        .order_by(Evidence.created_at.desc())
        .limit(TEXT_LOOKBACK_LIMIT)
        .all()
    )

    clean_new = _clean(text)
    for ev in recent:
        existing = ev.message_text or ""
        if not existing:
            continue
        ratio = difflib.SequenceMatcher(None, clean_new, _clean(existing)).ratio()
        if ratio >= TEXT_DUPLICATE_RATIO:
            logger.info(
                "[DEDUP] near-duplicate text ratio=%.2f teacher_id=%d existing_id=%d",
                ratio, teacher_id, ev.id,
            )
            return ev

    return None


# ──────────────────────────────────────────────────────────────────────────────
# Export-time deduplication (kept here — only consumed by export pipeline)
# ──────────────────────────────────────────────────────────────────────────────


def _best_of(a: dict, b: dict) -> dict:
    score_a = len(a.get("description") or "") + len(a.get("title") or "") * 0.5
    score_b = len(b.get("description") or "") + len(b.get("title") or "") * 0.5
    return a if score_a >= score_b else b


def _title_key(title: str) -> str:
    return _clean(title).lower()


def deduplicate_for_export(evidences: list[dict]) -> list[dict]:
    """Drop duplicates from a normalised evidence list before PDF render.

    Two passes: content_hash, then (category, title) within the same
    category. Trivial titles are skipped so multiple visual evidences
    keep their own card.
    """
    _TRIVIAL_TITLES = {
        _title_key(t) for t in (
            "نشاط تعليمي موثق بالصورة", "مقطع مرئي تعليمي موثق",
            "تسجيل صوتي تعليمي", "ملاحظة صوتية تعليمية",
            "ملف تعليمي مرفق", "وثيقة تعليمية pdf",
            "مصدر رقمي موثق", "ملاحظة تعليمية موثقة",
            "شاهد تعليمي موثق من المعلم",
        )
    }

    seen_hashes: dict[str, dict] = {}
    no_hash: list[dict] = []
    for ev in evidences:
        h = (ev.get("content_hash") or "").strip()
        if h:
            if h in seen_hashes:
                seen_hashes[h] = _best_of(seen_hashes[h], ev)
            else:
                seen_hashes[h] = ev
        else:
            no_hash.append(ev)

    deduped_by_hash = list(seen_hashes.values()) + no_hash

    seen_titles: dict[tuple[str, str], dict] = {}
    result: list[dict] = []
    for ev in deduped_by_hash:
        tk = _title_key(ev.get("title") or "")
        cat = ev.get("category") or ""
        key = (cat, tk)

        if tk in _TRIVIAL_TITLES:
            result.append(ev)
            continue

        if key in seen_titles:
            seen_titles[key] = _best_of(seen_titles[key], ev)
            logger.info(
                "[EXPORT DEDUP] title duplicate skipped: %r in category %r",
                ev.get("title"), cat,
            )
        else:
            seen_titles[key] = ev
            result.append(ev)

    n_removed = len(evidences) - len(result)
    if n_removed:
        logger.info("[EXPORT DEDUP] removed %d duplicate(s) before PDF render", n_removed)

    return result


# Re-exports for backwards compatibility.
__all__ = [
    "TEXT_DUPLICATE_RATIO", "TEXT_MIN_LENGTH",
    "TEXT_LOOKBACK_DAYS", "TEXT_LOOKBACK_LIMIT",
    "hash_bytes", "hash_text", "hash_url",
    "is_exact_duplicate", "get_evidence_by_hash",
    "find_near_duplicate_text", "deduplicate_for_export",
]
