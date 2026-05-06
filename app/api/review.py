"""
Evidence review page for teachers — /review/{token}

GET  /review/{token}              — Review page (HTML) showing all evidences
POST /review/{token}/toggle/{id}  — Toggle is_excluded_from_export (AJAX auto-save)
POST /review/{token}/export       — Start export mode selection (sends WA message)
"""
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.base import get_db
from app.models.evidence import Evidence
from app.services.teachers import get_teacher_by_review_token
from app.services.evidences import get_teacher_evidences

logger = logging.getLogger(__name__)
router = APIRouter()

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
_jinja = Environment(loader=FileSystemLoader(str(_TEMPLATES_DIR)), autoescape=True)


def _public_thumb(ev) -> str | None:
    """Return a public /files URL for image thumbnails in the review page."""
    storage_path = ev.storage_path
    if not storage_path:
        return None
    file_path = Path(storage_path)
    try:
        rel = file_path.resolve().relative_to(settings.storage_path.resolve())
        return f"{settings.effective_base_url}/files/{rel.as_posix()}"
    except Exception:
        return None


@router.get("/review/{token}", response_class=HTMLResponse)
def review_page(token: str, db: Session = Depends(get_db)):
    teacher = get_teacher_by_review_token(db, token)
    if not teacher:
        raise HTTPException(status_code=404, detail="رابط غير صالح")

    evidences = get_teacher_evidences(db, teacher.id, limit=1000)

    # Build lightweight display dicts — no base64, just thumb URLs
    items = []
    for ev in evidences:
        thumb = None
        if ev.evidence_type in ("image", "video"):
            thumb = _public_thumb(ev)
        items.append({
            "id": ev.id,
            "title": ev.title or "بدون عنوان",
            "category": ev.category or "أخرى",
            "evidence_type": ev.evidence_type or "text",
            "created_at": ev.created_at.strftime("%Y/%m/%d") if ev.created_at else "",
            "is_excluded": ev.is_excluded_from_export,
            "thumb": thumb,
            "description": (ev.description or ev.message_text or "")[:160],
        })

    export_url = f"{settings.effective_base_url}/review/{token}/export"
    template = _jinja.get_template("review.html")
    html = template.render(
        teacher=teacher,
        items=items,
        token=token,
        export_url=export_url,
        base_url=settings.effective_base_url,
    )
    return HTMLResponse(html)


@router.post("/review/{token}/toggle/{evidence_id}")
def toggle_exclude(token: str, evidence_id: int, db: Session = Depends(get_db)):
    teacher = get_teacher_by_review_token(db, token)
    if not teacher:
        raise HTTPException(status_code=404, detail="رابط غير صالح")

    ev = (
        db.query(Evidence)
        .filter(Evidence.id == evidence_id, Evidence.teacher_id == teacher.id)
        .first()
    )
    if not ev:
        raise HTTPException(status_code=404, detail="الشاهد غير موجود")

    ev.is_excluded_from_export = not ev.is_excluded_from_export
    db.commit()
    logger.info(
        "[REVIEW TOGGLE] teacher_id=%d evidence_id=%d excluded=%s",
        teacher.id, ev.id, ev.is_excluded_from_export,
    )
    return JSONResponse({"excluded": ev.is_excluded_from_export})


@router.post("/review/{token}/export")
async def trigger_export_from_review(token: str, db: Session = Depends(get_db)):
    """Send the export-mode selection WhatsApp message and mark teacher as pending."""
    from app.services.teachers import get_or_create_review_token
    from app.services.whatsapp import send_export_options_buttons
    import asyncio

    teacher = get_teacher_by_review_token(db, token)
    if not teacher:
        raise HTTPException(status_code=404, detail="رابط غير صالح")

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
