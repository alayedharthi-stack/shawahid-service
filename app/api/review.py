"""
Evidence review page — /review/{token}

Phase-5 architecture
--------------------
This file is a **thin adapter** between the HTTP layer and
``app.review_engine``. It:

    1. Fetches raw evidence rows from the DB.
    2. Delegates all logic to ``review_engine``.
    3. Renders HTML via Jinja (the template itself is not part of the
       engine).

Routes
------
GET  /review/{token}                  Review page (HTML)
POST /review/{token}/toggle/{id}      Toggle exclude (legacy, kept for compat)
POST /review/{token}/approve/{id}     Mark included in export
POST /review/{token}/delete/{id}      Soft-delete (exclude from export)
POST /review/{token}/restore/{id}     Undo soft-delete
POST /review/{token}/update_title/{id}    Edit title (JSON body: {title})
POST /review/{token}/update_category/{id} Edit category (JSON body: {category})
POST /review/{token}/export           Send export-mode WhatsApp message
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.base import get_db
from app.services.teachers import get_teacher_by_review_token
from app.services.evidences import get_teacher_evidences, ALLOWED_CATEGORIES
from app.review_engine import (
    build_review_session,
    approve_evidence,
    delete_evidence,
    restore_evidence,
    toggle_exclude,
    update_evidence_category,
    update_evidence_title,
)

logger = logging.getLogger(__name__)
router = APIRouter()

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
_jinja = Environment(loader=FileSystemLoader(str(_TEMPLATES_DIR)), autoescape=True)


def _get_teacher_or_404(db: Session, token: str):
    teacher = get_teacher_by_review_token(db, token)
    if not teacher:
        raise HTTPException(status_code=404, detail="رابط غير صالح")
    return teacher


# ── Pages ─────────────────────────────────────────────────────────────


@router.get("/review/{token}", response_class=HTMLResponse)
def review_page(token: str, db: Session = Depends(get_db)):
    teacher = _get_teacher_or_404(db, token)
    evidences = get_teacher_evidences(db, teacher.id, limit=1000)

    session = build_review_session(
        evidences,
        teacher_id=teacher.id,
        teacher_name=teacher.name,
        base_url=settings.effective_base_url,
    )

    export_url = f"{settings.effective_base_url}/review/{token}/export"
    template = _jinja.get_template("review.html")
    html = template.render(
        teacher=teacher,
        session=session,
        token=token,
        export_url=export_url,
        base_url=settings.effective_base_url,
        all_categories=ALLOWED_CATEGORIES,
        # Legacy: keep 'items' available in case any old code reads it
        items=[],
    )
    return HTMLResponse(html)


# ── Evidence actions ───────────────────────────────────────────────────


@router.post("/review/{token}/toggle/{evidence_id}")
def toggle_exclude_route(token: str, evidence_id: int, db: Session = Depends(get_db)):
    teacher = _get_teacher_or_404(db, token)
    result = toggle_exclude(db, evidence_id, teacher.id)
    if not result["ok"]:
        raise HTTPException(status_code=404, detail="الشاهد غير موجود")
    logger.info(
        "[REVIEW TOGGLE] teacher=%d evidence=%d excluded=%s",
        teacher.id, evidence_id, result["is_excluded"],
    )
    return JSONResponse({"excluded": result["is_excluded"]})


@router.post("/review/{token}/approve/{evidence_id}")
def approve_route(token: str, evidence_id: int, db: Session = Depends(get_db)):
    teacher = _get_teacher_or_404(db, token)
    result = approve_evidence(db, evidence_id, teacher.id)
    if not result["ok"]:
        raise HTTPException(status_code=404, detail="الشاهد غير موجود")
    return JSONResponse(result)


@router.post("/review/{token}/delete/{evidence_id}")
def delete_route(token: str, evidence_id: int, db: Session = Depends(get_db)):
    teacher = _get_teacher_or_404(db, token)
    result = delete_evidence(db, evidence_id, teacher.id)
    if not result["ok"]:
        raise HTTPException(status_code=404, detail="الشاهد غير موجود")
    return JSONResponse(result)


@router.post("/review/{token}/restore/{evidence_id}")
def restore_route(token: str, evidence_id: int, db: Session = Depends(get_db)):
    teacher = _get_teacher_or_404(db, token)
    result = restore_evidence(db, evidence_id, teacher.id)
    if not result["ok"]:
        raise HTTPException(status_code=404, detail="الشاهد غير موجود")
    return JSONResponse(result)


@router.post("/review/{token}/update_title/{evidence_id}")
async def update_title_route(
    token: str, evidence_id: int, request: Request, db: Session = Depends(get_db)
):
    teacher = _get_teacher_or_404(db, token)
    body = await request.json()
    new_title = (body.get("title") or "").strip()
    result = update_evidence_title(db, evidence_id, teacher.id, new_title)
    if not result["ok"]:
        raise HTTPException(
            status_code=422 if "empty" in result.get("error", "") else 404,
            detail=result.get("error"),
        )
    return JSONResponse(result)


@router.post("/review/{token}/update_category/{evidence_id}")
async def update_category_route(
    token: str, evidence_id: int, request: Request, db: Session = Depends(get_db)
):
    teacher = _get_teacher_or_404(db, token)
    body = await request.json()
    new_cat = (body.get("category") or "").strip()
    result = update_evidence_category(db, evidence_id, teacher.id, new_cat)
    if not result["ok"]:
        raise HTTPException(
            status_code=422 if "empty" in result.get("error", "") else 404,
            detail=result.get("error"),
        )
    return JSONResponse(result)


# ── Export trigger ─────────────────────────────────────────────────────


@router.post("/review/{token}/export")
async def trigger_export_from_review(token: str, db: Session = Depends(get_db)):
    """Send the export-mode selection WhatsApp message and mark teacher as pending."""
    import asyncio
    from app.services.teachers import get_or_create_review_token
    from app.services.whatsapp import send_export_options_buttons

    teacher = _get_teacher_or_404(db, token)

    # Mark teacher as having a pending export so the mode-selection shortcut works
    from app.api.webhook import _PENDING_EXPORT_REQUESTS
    _PENDING_EXPORT_REQUESTS.add(teacher.id)

    asyncio.create_task(
        send_export_options_buttons(teacher.phone, teacher_id=teacher.id)
    )
    logger.info("[REVIEW EXPORT TRIGGER] teacher_id=%d", teacher.id)
    return JSONResponse({
        "ok": True,
        "message": "سيصلك رسالة واتساب لاختيار نوع التصدير",
    })
