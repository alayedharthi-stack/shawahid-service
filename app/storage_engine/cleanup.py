"""
storage_engine.cleanup — read-only audit tools for storage hygiene.

These helpers NEVER delete anything. They walk the filesystem and the
DB to surface inconsistencies, leaving the operator to decide what to
do. They exist so we can spot orphaned files / broken paths before
the curriculum_engine work piles on more data.

Three audit reports:

    • find_orphan_files()        — files on disk with no matching DB row
    • find_missing_files()       — DB rows whose ``storage_path`` is gone
    • find_broken_evidence_paths() — DB rows with malformed paths

All three return plain dataclasses so callers can serialise the report
to logs, JSON, or a future admin endpoint.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.evidence import Evidence
from app.storage_engine.file_store import file_exists
from app.storage_engine.paths import (
    ensure_within_storage_root,
    storage_root,
    teacher_root,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Report DTOs
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class OrphanFile:
    path: str
    teacher_id: int | None
    size_bytes: int


@dataclass(frozen=True)
class MissingFileRow:
    evidence_id: int
    teacher_id: int
    expected_path: str


@dataclass(frozen=True)
class BrokenPathRow:
    evidence_id: int
    teacher_id: int
    raw_path: str
    reason: str


@dataclass(frozen=True)
class CleanupReport:
    orphan_files: list[OrphanFile] = field(default_factory=list)
    missing_files: list[MissingFileRow] = field(default_factory=list)
    broken_paths: list[BrokenPathRow] = field(default_factory=list)

    @property
    def total_issues(self) -> int:
        return len(self.orphan_files) + len(self.missing_files) + len(self.broken_paths)


# ──────────────────────────────────────────────────────────────────────────────
# Audits
# ──────────────────────────────────────────────────────────────────────────────


def find_orphan_files(
    db: Session,
    *,
    teacher_id: int | None = None,
) -> list[OrphanFile]:
    """Return files on disk that have no matching ``Evidence.storage_path``.

    Scoped to one teacher when ``teacher_id`` is provided; otherwise scans
    every ``teachers/*`` directory under the storage root.
    """
    db_paths: set[str] = set()
    q = db.query(Evidence.teacher_id, Evidence.storage_path).filter(
        Evidence.storage_path.isnot(None)
    )
    if teacher_id is not None:
        q = q.filter(Evidence.teacher_id == teacher_id)
    for _t, sp in q.all():
        if sp:
            db_paths.add(str(Path(sp)))

    orphans: list[OrphanFile] = []
    teachers_root = storage_root() / "teachers"
    if not teachers_root.exists():
        return orphans

    teacher_dirs: list[tuple[int | None, Path]]
    if teacher_id is not None:
        teacher_dirs = [(teacher_id, teacher_root(teacher_id))]
    else:
        teacher_dirs = []
        for d in teachers_root.iterdir():
            if not d.is_dir():
                continue
            try:
                tid = int(d.name)
            except ValueError:
                tid = None
            teacher_dirs.append((tid, d))

    for tid, root_dir in teacher_dirs:
        for f in root_dir.rglob("*"):
            if not f.is_file():
                continue
            full = str(f)
            if full in db_paths:
                continue
            try:
                size = f.stat().st_size
            except OSError:
                size = 0
            orphans.append(OrphanFile(path=full, teacher_id=tid, size_bytes=size))

    return orphans


def find_missing_files(
    db: Session,
    *,
    teacher_id: int | None = None,
) -> list[MissingFileRow]:
    """Return DB rows whose ``storage_path`` no longer points to a file."""
    q = db.query(
        Evidence.id, Evidence.teacher_id, Evidence.storage_path
    ).filter(Evidence.storage_path.isnot(None))
    if teacher_id is not None:
        q = q.filter(Evidence.teacher_id == teacher_id)

    missing: list[MissingFileRow] = []
    for ev_id, tid, sp in q.all():
        if not sp:
            continue
        if not file_exists(sp):
            missing.append(MissingFileRow(
                evidence_id=int(ev_id),
                teacher_id=int(tid),
                expected_path=str(sp),
            ))
    return missing


def find_broken_evidence_paths(
    db: Session,
    *,
    teacher_id: int | None = None,
) -> list[BrokenPathRow]:
    """Return rows whose ``storage_path`` is malformed — escaping the
    storage root or otherwise unresolvable.
    """
    q = db.query(
        Evidence.id, Evidence.teacher_id, Evidence.storage_path
    ).filter(Evidence.storage_path.isnot(None))
    if teacher_id is not None:
        q = q.filter(Evidence.teacher_id == teacher_id)

    broken: list[BrokenPathRow] = []
    for ev_id, tid, sp in q.all():
        if not sp:
            continue
        try:
            ensure_within_storage_root(sp)
        except ValueError as exc:
            broken.append(BrokenPathRow(
                evidence_id=int(ev_id),
                teacher_id=int(tid),
                raw_path=str(sp),
                reason=str(exc),
            ))
    return broken


def build_cleanup_report(
    db: Session,
    *,
    teacher_id: int | None = None,
) -> CleanupReport:
    """One-shot helper that collects all three reports."""
    return CleanupReport(
        orphan_files=find_orphan_files(db, teacher_id=teacher_id),
        missing_files=find_missing_files(db, teacher_id=teacher_id),
        broken_paths=find_broken_evidence_paths(db, teacher_id=teacher_id),
    )
