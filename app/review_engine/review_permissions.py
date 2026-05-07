"""
review_permissions — lightweight ownership and access checks.

All checks are pure functions — no DB, no ORM, no Playwright.
The DB-level ownership check (teacher_id on each evidence row) is
enforced by review_actions._fetch_owned; this module handles the
higher-level "can this token holder do X" logic.
"""
from __future__ import annotations


def can_review(requesting_teacher_id: int, session_teacher_id: int) -> bool:
    """Return True if the requesting teacher owns the review session."""
    return requesting_teacher_id == session_teacher_id


def can_export(session_teacher_id: int, *, active_items: int) -> bool:
    """Return True when the teacher has at least one active evidence
    and is therefore allowed to start an export."""
    _ = session_teacher_id  # reserved for future subscription check
    return active_items > 0


def can_delete(requesting_teacher_id: int, evidence_teacher_id: int) -> bool:
    """Return True when the requesting teacher owns the evidence."""
    return requesting_teacher_id == evidence_teacher_id


def can_restore(requesting_teacher_id: int, evidence_teacher_id: int) -> bool:
    """Return True when the requesting teacher owns the evidence."""
    return requesting_teacher_id == evidence_teacher_id


def can_edit(requesting_teacher_id: int, evidence_teacher_id: int) -> bool:
    """Return True when the requesting teacher owns the evidence."""
    return requesting_teacher_id == evidence_teacher_id
