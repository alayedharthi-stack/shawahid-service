"""
storage_engine.dedup — content-hash based duplicate detection.

Scope rules (enforced by tests):
    • Duplicates are *only* detected within the same teacher_id.
    • The same byte payload uploaded by a different teacher is NOT a
      duplicate — every teacher owns their own evidence space.
    • This module reads/writes the ORM but contains zero business
      logic about WhatsApp / export / review — it is a pure dedup
      primitive.

The DB has no dedicated ``duplicate_of_id`` column; we persist that
field inside the existing ``ai_raw`` JSONB blob to keep this phase
migration-free.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models.evidence import Evidence

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Lookup
# ──────────────────────────────────────────────────────────────────────────────


def find_duplicate_by_hash(
    db: Session,
    teacher_id: int,
    content_hash: str,
) -> Evidence | None:
    """Return the most recent Evidence row for ``teacher_id`` whose
    ``content_hash`` matches.

    Returns ``None`` when no duplicate exists. The teacher_id filter is
    *mandatory* — never look up a hash globally.
    """
    if not content_hash:
        return None
    if not isinstance(teacher_id, int) or teacher_id <= 0:
        return None
    return (
        db.query(Evidence)
        .filter(
            Evidence.teacher_id == teacher_id,
            Evidence.content_hash == content_hash,
        )
        .order_by(Evidence.created_at.desc())
        .first()
    )


def is_duplicate_for_teacher(
    db: Session,
    teacher_id: int,
    content_hash: str,
) -> bool:
    """Boolean-only convenience wrapper around ``find_duplicate_by_hash``."""
    return find_duplicate_by_hash(db, teacher_id, content_hash) is not None


# ──────────────────────────────────────────────────────────────────────────────
# Mark duplicate (soft)
# ──────────────────────────────────────────────────────────────────────────────


def mark_duplicate(
    db: Session,
    evidence_id: int,
    duplicate_of_id: int,
) -> bool:
    """Mark ``evidence_id`` as a duplicate of ``duplicate_of_id``.

    Side effects (always atomic, single commit):
        • Sets ``is_excluded_from_export = True`` so the PDF export
          skips the duplicate without losing the row entirely.
        • Records ``ai_raw["duplicate_of_id"]`` and
          ``ai_raw["is_duplicate"] = True`` so future readers can
          surface the relationship.

    Returns ``True`` on success, ``False`` if either row is missing
    or the two rows belong to different teachers (cross-teacher
    duplication is never allowed).
    """
    if evidence_id == duplicate_of_id:
        logger.warning("[DEDUP] refusing to mark evidence %d as duplicate of itself", evidence_id)
        return False

    ev = db.query(Evidence).filter(Evidence.id == evidence_id).first()
    target = db.query(Evidence).filter(Evidence.id == duplicate_of_id).first()
    if not ev or not target:
        logger.warning(
            "[DEDUP] mark_duplicate skipped: evidence_id=%d target=%d (one is missing)",
            evidence_id, duplicate_of_id,
        )
        return False
    if ev.teacher_id != target.teacher_id:
        logger.warning(
            "[DEDUP] cross-teacher duplicate ignored: ev_id=%d ev_teacher=%d target_teacher=%d",
            evidence_id, ev.teacher_id, target.teacher_id,
        )
        return False

    ev.is_excluded_from_export = True
    raw = dict(ev.ai_raw or {})
    raw["is_duplicate"] = True
    raw["duplicate_of_id"] = duplicate_of_id
    ev.ai_raw = raw
    db.commit()
    logger.info(
        "[DEDUP] evidence_id=%d marked as duplicate of %d (teacher_id=%d)",
        evidence_id, duplicate_of_id, ev.teacher_id,
    )
    return True
