"""
storage_engine.schemas — pure data transfer objects for the storage layer.

Hard rules (enforced by Phase-7 architectural tests):
    • No SQLAlchemy / ORM imports here.
    • No Playwright, no export_engine.
    • Pure ``@dataclass`` types, never serialised to JSON automatically.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


# ──────────────────────────────────────────────────────────────────────────────
# 1. StoredFile
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StoredFile:
    """A file that has been persisted to the storage layer.

    All fields are populated at write-time. ``public_path`` is the
    ``/files/...`` URL (relative to the deployment base URL) that the
    media_engine resolves; it may be ``None`` for files that should
    never be served publicly (e.g. raw audio backups).
    """

    original_filename: str | None
    stored_path: str
    public_path: str | None
    mime_type: str | None
    file_size: int
    content_hash: str
    storage_bucket: str = "teachers"
    created_at: datetime | None = None


# ──────────────────────────────────────────────────────────────────────────────
# 2. EvidenceStorageRef
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EvidenceStorageRef:
    """Storage references attached to one Evidence row.

    Decouples evidence-row consumers (review_engine, export_engine, media_engine)
    from raw ORM access — each consumer can read this DTO instead of poking
    at ORM attributes directly.

    ``duplicate_of_id`` is populated by ``dedup.mark_duplicate`` and stored
    inside the existing ``ai_raw`` JSON blob — there is *no* dedicated DB
    column to keep this phase migration-free.
    """

    evidence_id: int
    teacher_id: int
    file_path: str | None = None
    preview_path: str | None = None
    thumbnail_path: str | None = None
    content_hash: str | None = None
    is_duplicate: bool = False
    duplicate_of_id: int | None = None
