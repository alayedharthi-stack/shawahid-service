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
import mimetypes
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

# Video file extensions (used when resolving actual video from thumbnail path)
_VIDEO_EXTS = (".mp4", ".mpeg4", ".mov", ".webm", ".avi", ".mkv", ".m4v")
_AUDIO_EXTS = (".ogg", ".mp3", ".m4a", ".wav", ".opus", ".aac", ".flac")


def _resolve_video_url(ev, base_url: str) -> str | None:
    """
    For video evidence, the DB storage_path holds the thumbnail (.thumb.jpg).
    Find the actual video file in the same directory using ev.file_name.
    Falls back to looking for any video file with the same stem.
    """
    sp = ev.storage_path
    if not sp:
        return None

    sp_path = Path(sp)

    # Case 1: storage_path is a thumbnail — find original video via file_name
    if sp_path.name.endswith(".thumb.jpg") and ev.file_name:
        video_candidate = sp_path.parent / ev.file_name
        if video_candidate.exists():
            return storage_path_to_file_url(str(video_candidate), base_url)

    # Case 2: storage_path is already a video file
    if sp_path.suffix.lower() in _VIDEO_EXTS and sp_path.exists():
        return storage_path_to_file_url(sp, base_url)

    # Case 3: derive video path from thumbnail stem (strip .thumb.jpg → try extensions)
    if sp_path.name.endswith(".thumb.jpg"):
        stem = sp_path.name[: -len(".thumb.jpg")]
        for ext in _VIDEO_EXTS:
            candidate = sp_path.parent / (stem + ext)
            if candidate.exists():
                return storage_path_to_file_url(str(candidate), base_url)

    return None


def _resolve_audio_url(ev, base_url: str) -> str | None:
    """Return the public URL for an audio evidence file with a correct path."""
    sp = ev.storage_path
    if not sp:
        return None
    sp_path = Path(sp)
    if sp_path.exists():
        return storage_path_to_file_url(sp, base_url)
    # Try via file_name in the same directory
    if ev.file_name:
        candidate = sp_path.parent / ev.file_name
        if candidate.exists():
            return storage_path_to_file_url(str(candidate), base_url)
    return None


def _guess_mime(path_str: str | None, stored_mime: str | None, ev_type: str) -> str:
    """Best-effort MIME type from stored value, filename, or evidence type."""
    if stored_mime:
        return stored_mime
    if path_str:
        guessed = mimetypes.guess_type(path_str)[0]
        if guessed:
            return guessed
    defaults = {
        "video": "video/mp4",
        "audio": "audio/mpeg",
        "voice": "audio/ogg",
        "pdf":   "application/pdf",
        "image": "image/jpeg",
    }
    return defaults.get(ev_type, "application/octet-stream")


def _build_viewer_html(ev, file_url: str | None, base_url: str, *,
                       video_url: str | None = None,
                       audio_url: str | None = None,
                       video_mime: str = "video/mp4",
                       audio_mime: str = "audio/mpeg") -> str:
    """Generate a simple responsive HTML media viewer for an evidence."""
    title   = (ev.title or "ملف مرفق").replace("<", "&lt;").replace(">", "&gt;")
    ev_type = (ev.evidence_type or "").lower()
    fname   = (ev.file_name or "").replace("<", "&lt;").replace(">", "&gt;")
    # Download URL — use /media/{id}/download for controlled MIME serving
    dl_url  = f"{base_url}/media/{ev.id}/download"

    # ── Body content based on type ────────────────────────────────────────────
    if ev_type in ("video",):
        play_url = video_url or file_url
        if play_url:
            content = f"""
<div class="player-wrap">
  <video controls playsinline preload="metadata"
         style="width:100%;max-width:100%;border-radius:12px;display:block;"
         onerror="document.getElementById('video-err-{ev.id}').style.display='block'">
    <source src="{play_url}" type="{video_mime}">
    <source src="{play_url}">
    <p>المتصفح لا يدعم تشغيل الفيديو.</p>
  </video>
  <div id="video-err-{ev.id}" style="display:none;margin-top:12px;">
    <p class="err">تعذّر تشغيل الفيديو في المتصفح.</p>
  </div>
</div>
<div style="margin-top:16px;display:flex;gap:12px;flex-wrap:wrap;justify-content:center;">
  <a class="btn-dl" href="{dl_url}" download="{fname or 'video.mp4'}">⬇ تحميل الفيديو</a>
</div>"""
        else:
            content = "<p class='err'>الفيديو غير متاح — ربما انتهت صلاحية الرابط الأصلي.</p>"

    elif ev_type in ("audio", "voice"):
        play_url = audio_url or file_url
        if play_url:
            # WhatsApp voice notes are ogg/opus; videos extract mp3 audio
            content = f"""
<div class="player-wrap">
  <audio controls preload="metadata"
         style="width:100%;max-width:600px;display:block;"
         onerror="document.getElementById('audio-err-{ev.id}').style.display='block'">
    <source src="{play_url}" type="{audio_mime}">
    <source src="{play_url}">
    <p>المتصفح لا يدعم تشغيل الصوت.</p>
  </audio>
  <div id="audio-err-{ev.id}" style="display:none;margin-top:12px;">
    <p class="err">تعذّر تشغيل الصوت في المتصفح.</p>
  </div>
</div>
<div style="margin-top:16px;display:flex;gap:12px;flex-wrap:wrap;justify-content:center;">
  <a class="btn-dl" href="{dl_url}" download="{fname or 'audio.mp3'}">⬇ تحميل التسجيل الصوتي</a>
</div>"""
        else:
            content = "<p class='err'>الملف الصوتي غير متاح.</p>"

    elif ev_type in ("pdf", "document") or (fname and fname.lower().endswith(".pdf")):
        if file_url:
            content = f"""
<div class="pdf-wrap">
  <iframe src="{file_url}" title="{title}" allowfullscreen
          style="width:100%;height:85vh;min-height:480px;border:none;display:block;"></iframe>
</div>
<div style="margin-top:16px;display:flex;gap:12px;flex-wrap:wrap;justify-content:center;">
  <a class="btn-primary" href="{file_url}" target="_blank">🔗 فتح في نافذة جديدة</a>
  <a class="btn-dl" href="{dl_url}" download="{fname or 'file.pdf'}">⬇ تحميل PDF</a>
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
<div style="margin-top:16px;display:flex;gap:12px;flex-wrap:wrap;justify-content:center;">
  <a class="btn-dl" href="{dl_url}" download="{fname or 'image.jpg'}">⬇ تحميل الصورة</a>
</div>"""
        else:
            content = "<p class='err'>الصورة غير متاحة.</p>"

    elif file_url:
        content = f"""
<div class="file-card">
  <span style="font-size:56px;">🗂️</span>
  <p style="margin:12px 0 4px;font-size:18px;font-weight:700;">{fname or title}</p>
  <p style="color:#666;margin-bottom:20px;">ملف مرفق</p>
</div>
<div style="display:flex;gap:12px;flex-wrap:wrap;justify-content:center;">
  <a class="btn-primary" href="{file_url}" target="_blank">🔗 فتح الملف</a>
  <a class="btn-dl" href="{dl_url}" download>⬇ تحميل الملف</a>
</div>"""

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
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>{title}</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{
    font-family: "Segoe UI", Tahoma, sans-serif;
    background: #f0f4f8; color: #1a1a2e; min-height: 100vh;
    overflow-x: hidden; max-width: 100vw;
  }}
  body {{
    display: flex; flex-direction: column; align-items: center;
  }}
  header {{
    width: 100%; background: #0f6f70; color: #fff;
    padding: 18px 16px; text-align: center;
  }}
  header h1 {{ font-size: 17px; font-weight: 700; margin-bottom: 4px;
               word-break: break-word; }}
  .badge {{
    display: inline-block; background: rgba(255,255,255,.2);
    border-radius: 999px; padding: 2px 12px; font-size: 12px;
  }}
  .container {{
    width: 100%; max-width: 900px; padding: 20px 12px;
    display: flex; flex-direction: column; align-items: center; gap: 16px;
  }}
  .desc {{ color: #555; font-size: 14px; text-align: center; max-width: 600px;
           line-height: 1.7; word-break: break-word; }}
  .player-wrap {{ width: 100%; display: flex; flex-direction: column; align-items: center; }}
  .pdf-wrap {{
    width: 100%; overflow: auto;
    border-radius: 8px;
    box-shadow: 0 4px 16px rgba(0,0,0,.1);
    max-width: 100vw;
  }}
  .img-wrap {{ width: 100%; display: flex; justify-content: center; overflow: hidden; }}
  .file-card {{ text-align: center; padding: 32px; background: #fff; width: 100%;
                border-radius: 16px; box-shadow: 0 4px 16px rgba(0,0,0,.08); }}
  .text-card {{ background: #fff; border-radius: 12px; padding: 24px 20px;
                max-width: 600px; line-height: 1.8; font-size: 15px;
                word-break: break-word; }}
  .err {{ color: #b91c1c; background: #fee2e2; border-radius: 8px;
          padding: 16px 20px; text-align: center; width: 100%; }}
  .btn-primary, .btn-dl {{
    display: inline-block; padding: 12px 24px; border-radius: 999px;
    font-size: 15px; font-weight: 600; text-decoration: none; cursor: pointer;
    transition: opacity .15s;
  }}
  .btn-primary {{ background: #0f6f70; color: #fff; }}
  .btn-dl {{ background: #fff; color: #0f6f70; border: 2px solid #0f6f70; }}
  .btn-primary:hover, .btn-dl:hover {{ opacity: .85; }}
  footer {{ margin-top: auto; padding: 16px; text-align: center;
            font-size: 12px; color: #888; }}
  @media (max-width: 480px) {{
    .btn-primary, .btn-dl {{ padding: 10px 16px; font-size: 14px; }}
    header h1 {{ font-size: 15px; }}
  }}
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

    base_url = settings.effective_base_url
    ev_type  = (ev.evidence_type or "").lower()

    # Default file_url from storage_path (thumbnail for video)
    file_url = storage_path_to_file_url(ev.storage_path, base_url)

    # If the evidence has a public media_url (e.g. non-WhatsApp CDN) and no local file, use it
    if not file_url and ev.media_url and not any(
        domain in (ev.media_url or "")
        for domain in ("fbsbx.com", "lookaside", "whatsapp.net")
    ):
        file_url = ev.media_url

    # ── Video: resolve actual video file (storage_path = thumbnail) ──────────
    video_url: str | None = None
    video_mime: str = "video/mp4"
    if ev_type == "video":
        video_url = _resolve_video_url(ev, base_url)
        stored_mime = (ev.mime_type or "").strip()
        if stored_mime and stored_mime.startswith("video/"):
            video_mime = stored_mime
        elif video_url:
            # Guess from the actual video filename
            guessed = mimetypes.guess_type(ev.file_name or "")[0]
            if guessed and guessed.startswith("video/"):
                video_mime = guessed
        if not video_url:
            logger.warning(
                "[MEDIA VIEWER] evidence_id=%d type=video — actual video file not found "
                "(storage_path=%s file_name=%s)",
                evidence_id, ev.storage_path, ev.file_name,
            )

    # ── Audio: resolve audio file and MIME ───────────────────────────────────
    audio_url: str | None = None
    audio_mime: str = "audio/mpeg"
    if ev_type in ("audio", "voice"):
        audio_url = _resolve_audio_url(ev, base_url)
        stored_mime = (ev.mime_type or "").strip()
        if stored_mime and stored_mime.startswith("audio/"):
            audio_mime = stored_mime
        elif ev.file_name:
            guessed = mimetypes.guess_type(ev.file_name)[0]
            if guessed and guessed.startswith("audio/"):
                audio_mime = guessed
        # WhatsApp voice notes are ogg/opus
        if ev_type == "voice" and audio_mime == "audio/mpeg":
            audio_mime = "audio/ogg; codecs=opus"

    logger.info(
        "[MEDIA VIEWER] evidence_id=%d type=%s file_url=%s video_url=%s audio_url=%s",
        evidence_id, ev_type, bool(file_url), bool(video_url), bool(audio_url),
    )

    html = _build_viewer_html(
        ev, file_url, base_url,
        video_url=video_url,
        audio_url=audio_url,
        video_mime=video_mime,
        audio_mime=audio_mime,
    )
    return HTMLResponse(content=html)


@router.get("/{evidence_id}/download")
async def media_download(evidence_id: int, db: Session = Depends(get_db)):
    """
    Direct file download for an evidence with correct MIME type headers.
    For video evidence: resolves the actual video file (not the thumbnail).
    """
    ev = get_evidence_by_id(db, evidence_id)
    if not ev:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="الشاهد غير موجود")

    if not ev.storage_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="لا يوجد ملف محفوظ")

    ev_type = (ev.evidence_type or "").lower()

    # For video: the DB storage_path is the thumbnail — find the actual video file
    if ev_type == "video":
        video_path = None
        sp = Path(ev.storage_path)
        if sp.name.endswith(".thumb.jpg") and ev.file_name:
            candidate = sp.parent / ev.file_name
            if candidate.exists():
                video_path = candidate
        if video_path is None:
            # Fall back to checking for any video extension
            if sp.name.endswith(".thumb.jpg"):
                stem = sp.name[: -len(".thumb.jpg")]
                for ext in _VIDEO_EXTS:
                    c = sp.parent / (stem + ext)
                    if c.exists():
                        video_path = c
                        break
        if video_path and video_path.is_file():
            filename = ev.file_name or video_path.name
            media_type = _guess_mime(str(video_path), ev.mime_type, "video")
            return FileResponse(
                str(video_path),
                filename=filename,
                media_type=media_type,
                headers={"Content-Disposition": f'inline; filename="{filename}"'},
            )

    path = Path(ev.storage_path)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="الملف غير موجود على الخادم")

    filename  = ev.file_name or path.name
    media_type = _guess_mime(str(path), ev.mime_type, ev_type)

    # Ensure video/audio get Range-request support (FastAPI FileResponse supports it natively)
    return FileResponse(
        str(path),
        filename=filename,
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )
