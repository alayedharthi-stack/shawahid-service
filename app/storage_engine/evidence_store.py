"""
storage_engine.evidence_store — high-level helpers that bridge a
:class:`StoredFile` with the ORM Evidence row.

Every other layer that touches Evidence storage attributes goes through
this module so the call sites read like prose:

    ref = build_evidence_storage_ref(evidence)
    if ref.is_duplicate:
        ...

and

    attach_file_to_evidence(db, evidence, stored_file)

Hard rules:
    • No path strings are constructed in callers; everything routes
      through ``storage_engine.paths``.
    • DB writes happen inside this module and commit immediately so the
      Evidence row is always in a consistent state.
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.evidence import Evidence
from app.storage_engine.file_store import file_exists
from app.storage_engine.schemas import EvidenceStorageRef, StoredFile

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Attach
# ──────────────────────────────────────────────────────────────────────────────


def attach_file_to_evidence(
    db: Session,
    evidence: Evidence,
    stored: StoredFile,
    *,
    commit: bool = True,
) -> Evidence:
    """Copy file metadata from ``stored`` onto ``evidence`` and commit."""
    evidence.storage_path = stored.stored_path
    evidence.file_name = stored.original_filename or evidence.file_name
    evidence.mime_type = stored.mime_type or evidence.mime_type
    evidence.content_hash = stored.content_hash or evidence.content_hash
    if commit:
        db.commit()
    return evidence


def attach_preview_to_evidence(
    db: Session,
    evidence: Evidence,
    preview_path: str | Path,
    *,
    commit: bool = True,
) -> Evidence:
    """Persist a preview path inside ``ai_raw["preview_path"]``.

    There is no dedicated DB column for previews — using ``ai_raw``
    keeps Phase-7 migration-free while still surfacing the field via
    :func:`build_evidence_storage_ref`.
    """
    raw = dict(evidence.ai_raw or {})
    raw["preview_path"] = str(preview_path)
    evidence.ai_raw = raw
    if commit:
        db.commit()
    return evidence


def attach_thumbnail_to_evidence(
    db: Session,
    evidence: Evidence,
    thumbnail_path: str | Path,
    *,
    commit: bool = True,
) -> Evidence:
    """Persist a thumbnail path inside ``ai_raw["thumbnail_path"]``."""
    raw = dict(evidence.ai_raw or {})
    raw["thumbnail_path"] = str(thumbnail_path)
    evidence.ai_raw = raw
    if commit:
        db.commit()
    return evidence


# ──────────────────────────────────────────────────────────────────────────────
# Build DTO
# ──────────────────────────────────────────────────────────────────────────────


def build_evidence_storage_ref(evidence: Evidence) -> EvidenceStorageRef:
    """Read all storage-related attributes off an Evidence row and return
    them as a pure DTO. Never raises — missing fields default to ``None``.
    """
    raw = evidence.ai_raw if isinstance(evidence.ai_raw, dict) else {}
    preview_path = raw.get("preview_path")
    thumbnail_path = raw.get("thumbnail_path")
    duplicate_of_id = raw.get("duplicate_of_id")
    is_duplicate = bool(raw.get("is_duplicate")) or duplicate_of_id is not None

    return EvidenceStorageRef(
        evidence_id=int(getattr(evidence, "id", 0) or 0),
        teacher_id=int(getattr(evidence, "teacher_id", 0) or 0),
        file_path=getattr(evidence, "storage_path", None),
        preview_path=preview_path,
        thumbnail_path=thumbnail_path,
        content_hash=getattr(evidence, "content_hash", None),
        is_duplicate=is_duplicate,
        duplicate_of_id=int(duplicate_of_id) if duplicate_of_id else None,
    )


def evidence_file_exists(evidence: Evidence) -> bool:
    """Convenience: does the Evidence row's stored file actually exist?"""
    return file_exists(getattr(evidence, "storage_path", None))
