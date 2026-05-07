"""
Media viewer endpoint — /media/{evidence_id}

Serves a standalone HTML page for each evidence that contains a file
(PDF, video, audio, image, document). This page is linked from:
- Buttons inside the portfolio PDF ("فتح ملف PDF", "مشاهدة الفيديو", etc.)
- The review page

The viewer is intentionally simple so it works across all browsers and
mobile WhatsApp in-app browsers (no heavy JS frameworks).

URL patterns:
  GET /media/{evidence_id}           → HTML viewer page
  GET /media/{evidence_id}/download  → Direct FileResponse for the file
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.base import get_db
from app.services.evidences import get_evidence_by_id
from app.services.storage import storage_path_to_file_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/media", tags=["media"])


def _build_viewer_html(ev, file_url: str | None, base_url: str) -> str:
    """Generate a simple responsive HTML media viewer for an evidence."""
    title   = (ev.title or "ملف مرفق").replace("<", "&lt;").replace(">", "&gt;")
    ev_type = (ev.evidence_type or "").lower()
    fname   = (ev.file_name or "").replace("<", "&lt;").replace(">", "&gt;")

    # ── Body content based on type ────────────────────────────────────────────
    if ev_type in ("video",):
        if file_url:
            content = f"""
<div class="player-wrap">
  <video controls playsinline preload="metadata" style="width:100%;max-width:720px;border-radius:12px;">
    <source src="{file_url}" type="video/mp4">
    <source src="{file_url}">
    <p>المتصفح لا يدعم تشغيل الفيديو.
       <a href="{file_url}" download>تحميل الفيديو</a></p>
  </video>
</div>
<a class="btn-dl" href="{file_url}" download>⬇ تحميل الفيديو</a>"""
        else:
            content = "<p class='err'>الفيديو غير متاح — ربما انتهت صلاحية الرابط الأصلي.</p>"

    elif ev_type in ("audio", "voice"):
        if file_url:
            content = f"""
<div class="player-wrap">
  <audio controls preload="metadata" style="width:100%;max-width:600px;">
    <source src="{file_url}">
    <p>المتصفح لا يدعم تشغيل الصوت.
       <a href="{file_url}" download>تحميل التسجيل</a></p>
  </audio>
</div>
<a class="btn-dl" href="{file_url}" download>⬇ تحميل التسجيل الصوتي</a>"""
        else:
            content = "<p class='err'>الملف الصوتي غير متاح.</p>"

    elif ev_type in ("pdf", "document") or (fname and fname.lower().endswith(".pdf")):
        if file_url:
            content = f"""
<div class="pdf-embed">
  <iframe src="{file_url}" title="{title}"
          style="width:100%;height:80vh;border:none;border-radius:8px;"></iframe>
</div>
<div style="margin-top:16px;display:flex;gap:12px;flex-wrap:wrap;justify-content:center;">
  <a class="btn-primary" href="{file_url}" target="_blank">🔗 فتح في نافذة جديدة</a>
  <a class="btn-dl" href="{file_url}" download="{fname or 'file.pdf'}">⬇ تحميل الملف</a>
</div>"""
        else:
            content = "<p class='err'>الملف غير متاح للمعاينة.</p>"

    elif ev_type in ("image",):
        if file_url:
            content = f"""
<div class="img-wrap">
  <img src="{file_url}" alt="{title}"
       style="max-width:100%;max-height:80vh;object-fit:contain;border-radius:8px;" />
</div>
<a class="btn-dl" href="{file_url}" download>⬇ تحميل الصورة</a>"""
        else:
            content = "<p class='err'>الصورة غير متاحة.</p>"

    elif file_url:
        content = f"""
<div class="file-card">
  <span style="font-size:56px;">🗂️</span>
  <p style="margin:12px 0 4px;font-size:18px;font-weight:700;">{fname or title}</p>
  <p style="color:#666;margin-bottom:20px;">ملف لا يمكن معاينته مباشرة</p>
</div>
<a class="btn-primary" href="{file_url}" target="_blank">🔗 فتح الملف</a>
<a class="btn-dl"     href="{file_url}" download>⬇ تحميل الملف</a>"""

    else:
        msg_text = (ev.message_text or "").strip()[:400]
        if msg_text:
            content = f"<div class='text-card'><p>{msg_text}</p></div>"
        else:
            content = "<p class='err'>لا يوجد ملف مرتبط بهذا الشاهد.</p>"

    desc = (ev.description or "").strip()[:200]
    desc_block = f"<p class='desc'>{desc}</p>" if desc else ""
    cat  = (ev.category or "").strip()
    cat_block = f"<span class='badge'>{cat}</span>" if cat else ""

    return f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: "Segoe UI", Tahoma, sans-serif;
    background: #f0f4f8; color: #1a1a2e; min-height: 100vh;
    display: flex; flex-direction: column; align-items: center;
  }}
  header {{
    width: 100%; background: #0f6f70; color: #fff;
    padding: 18px 24px; text-align: center;
  }}
  header h1 {{ font-size: 17px; font-weight: 700; margin-bottom: 4px; }}
  .badge {{
    display: inline-block; background: rgba(255,255,255,.2);
    border-radius: 999px; padding: 2px 12px; font-size: 12px;
  }}
  .container {{
    width: 100%; max-width: 860px; padding: 28px 16px;
    display: flex; flex-direction: column; align-items: center; gap: 16px;
  }}
  .desc {{ color: #555; font-size: 14px; text-align: center; max-width: 600px; line-height: 1.7; }}
  .player-wrap {{ width: 100%; display: flex; justify-content: center; }}
  .pdf-embed {{ width: 100%; border-radius: 8px; overflow: hidden;
                box-shadow: 0 4px 16px rgba(0,0,0,.1); }}
  .img-wrap {{ width: 100%; display: flex; justify-content: center; }}
  .file-card {{ text-align: center; padding: 32px; background: #fff;
                border-radius: 16px; box-shadow: 0 4px 16px rgba(0,0,0,.08); }}
  .text-card {{ background: #fff; border-radius: 12px; padding: 24px 28px;
                max-width: 600px; line-height: 1.8; font-size: 15px; }}
  .err {{ color: #b91c1c; background: #fee2e2; border-radius: 8px;
          padding: 16px 24px; }}
  .btn-primary, .btn-dl {{
    display: inline-block; padding: 12px 28px; border-radius: 999px;
    font-size: 15px; font-weight: 600; text-decoration: none; cursor: pointer;
    transition: opacity .15s;
  }}
  .btn-primary {{ background: #0f6f70; color: #fff; }}
  .btn-dl {{ background: #fff; color: #0f6f70; border: 2px solid #0f6f70; }}
  .btn-primary:hover, .btn-dl:hover {{ opacity: .85; }}
  footer {{ margin-top: auto; padding: 16px; text-align: center;
            font-size: 12px; color: #888; }}
</style>
</head>
<body>
<header>
  <h1>{title}</h1>
  {cat_block}
</header>
<div class="container">
  {desc_block}
  {content}
</div>
<footer>شواهد AI — ملف الإنجاز المهني للمعلم</footer>
</body>
</html>"""


@router.get("/{evidence_id}", response_class=HTMLResponse)
async def media_viewer(evidence_id: int, db: Session = Depends(get_db)):
    """
    Serve an HTML media viewer for a single evidence.
    The page is linked from buttons inside the exported portfolio PDF.
    """
    ev = get_evidence_by_id(db, evidence_id)
    if not ev:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="الشاهد غير موجود")

    base_url  = settings.effective_base_url
    file_url  = storage_path_to_file_url(ev.storage_path, base_url)

    # If the evidence has a public media_url (e.g. WhatsApp CDN) and no local file, use it
    if not file_url and ev.media_url and not any(
        domain in (ev.media_url or "")
        for domain in ("fbsbx.com", "lookaside", "whatsapp.net")
    ):
        file_url = ev.media_url

    logger.info(
        "[MEDIA VIEWER] evidence_id=%d type=%s file_url=%s",
        evidence_id, ev.evidence_type, bool(file_url),
    )

    html = _build_viewer_html(ev, file_url, base_url)
    return HTMLResponse(content=html)


@router.get("/{evidence_id}/download")
async def media_download(evidence_id: int, db: Session = Depends(get_db)):
    """
    Direct file download for an evidence.
    Falls back to 404 if no local storage_path is available.
    """
    ev = get_evidence_by_id(db, evidence_id)
    if not ev:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="الشاهد غير موجود")

    if not ev.storage_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="لا يوجد ملف محفوظ")

    path = Path(ev.storage_path)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="الملف غير موجود على الخادم")

    filename = ev.file_name or path.name
    return FileResponse(
        str(path),
        filename=filename,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )
