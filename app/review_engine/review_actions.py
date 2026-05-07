"""
review_actions — atomic DB actions for the review page.

Every function:
    1. Verifies ownership (teacher_id matches evidence.teacher_id).
    2. Performs exactly one side effect.
    3. Returns a result dict so the route can respond without touching
       the DB again.

Soft-delete contract
--------------------
Phase 5 must never physically delete an evidence row via this module.
"Delete" in the UI means ``is_excluded_from_export = True``.
"Restore" means ``is_excluded_from_export = False``.

Hard rules:
    • May import SQLAlchemy — this is the data-access boundary.
    • Must NOT import export_engine or Playwright.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_ALLOWED_CATEGORIES = [
    "التخطيط", "التنفيذ داخل الصف", "التعلم النشط", "التقويم",
    "التحفيز", "التعلم التعاوني", "سجل المتابعة",
    "المصادر والتقنية", "ملفات إدارية",
    # legacy
    "نشاط صفي", "تعلم تعاوني", "حل تمارين", "مشاركة طلابية",
    "تكريم وتميز", "شرح درس", "واجب منزلي", "اختبار", "ورقة عمل",
    "تقويم", "مصدر تعليمي", "رابط إثرائي", "ملف إداري", "إنجاز طلابي",
    "الدورات والشهادات", "المبادرات والأنشطة", "أخرى",
]


def _fetch_owned(db: Session, evidence_id: int, teacher_id: int):
    """Return the Evidence row or None if not found / wrong owner."""
    from app.models.evidence import Evidence
    return (
        db.query(Evidence)
        .filter(Evidence.id == evidence_id, Evidence.teacher_id == teacher_id)
        .first()
    )


# ── Actions ───────────────────────────────────────────────────────────


def approve_evidence(
    db: Session,
    evidence_id: int,
    teacher_id: int,
) -> dict:
    """Mark evidence as explicitly included in export.

    Has no visible effect if is_excluded is already False, but
    calling this makes the teacher's intent explicit in the logs.
    """
    ev = _fetch_owned(db, evidence_id, teacher_id)
    if not ev:
        return {"ok": False, "error": "evidence_not_found"}
    ev.is_excluded_from_export = False
    db.commit()
    logger.info("[REVIEW APPROVE] teacher=%d evidence=%d", teacher_id, evidence_id)
    return {"ok": True, "is_excluded": False}


def update_evidence_category(
    db: Session,
    evidence_id: int,
    teacher_id: int,
    new_category: str,
) -> dict:
    """Overwrite the category with a teacher-validated value."""
    ev = _fetch_owned(db, evidence_id, teacher_id)
    if not ev:
        return {"ok": False, "error": "evidence_not_found"}
    category = new_category.strip()
    if not category:
        return {"ok": False, "error": "empty_category"}
    ev.category = category
    db.commit()
    logger.info(
        "[REVIEW CAT UPDATE] teacher=%d evidence=%d category=%r",
        teacher_id, evidence_id, category,
    )
    return {"ok": True, "category": category}


def update_evidence_title(
    db: Session,
    evidence_id: int,
    teacher_id: int,
    new_title: str,
) -> dict:
    """Overwrite the title with a teacher-validated value."""
    ev = _fetch_owned(db, evidence_id, teacher_id)
    if not ev:
        return {"ok": False, "error": "evidence_not_found"}
    title = new_title.strip()
    if not title:
        return {"ok": False, "error": "empty_title"}
    ev.title = title
    db.commit()
    logger.info(
        "[REVIEW TITLE UPDATE] teacher=%d evidence=%d title=%r",
        teacher_id, evidence_id, title,
    )
    return {"ok": True, "title": title}


def delete_evidence(
    db: Session,
    evidence_id: int,
    teacher_id: int,
) -> dict:
    """Soft-delete: exclude from export. The row is never physically removed."""
    ev = _fetch_owned(db, evidence_id, teacher_id)
    if not ev:
        return {"ok": False, "error": "evidence_not_found"}
    ev.is_excluded_from_export = True
    db.commit()
    logger.info("[REVIEW SOFT-DELETE] teacher=%d evidence=%d", teacher_id, evidence_id)
    return {"ok": True, "is_excluded": True}


def mark_duplicate(
    db: Session,
    evidence_id: int,
    teacher_id: int,
) -> dict:
    """Convenience wrapper — mark as duplicate by soft-deleting.

    The teacher told us "هذا مكرر". We exclude from export without
    touching content_hash. The AI's duplicate detection still runs
    on its own schedule.
    """
    return delete_evidence(db, evidence_id, teacher_id)


def restore_evidence(
    db: Session,
    evidence_id: int,
    teacher_id: int,
) -> dict:
    """Undo a soft-delete: include in export again."""
    ev = _fetch_owned(db, evidence_id, teacher_id)
    if not ev:
        return {"ok": False, "error": "evidence_not_found"}
    ev.is_excluded_from_export = False
    db.commit()
    logger.info("[REVIEW RESTORE] teacher=%d evidence=%d", teacher_id, evidence_id)
    return {"ok": True, "is_excluded": False}


def toggle_exclude(
    db: Session,
    evidence_id: int,
    teacher_id: int,
) -> dict:
    """Legacy toggle used by the existing /toggle/{id} route."""
    ev = _fetch_owned(db, evidence_id, teacher_id)
    if not ev:
        return {"ok": False, "error": "evidence_not_found"}
    ev.is_excluded_from_export = not ev.is_excluded_from_export
    db.commit()
    return {"ok": True, "is_excluded": ev.is_excluded_from_export}
