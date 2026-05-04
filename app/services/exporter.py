"""
Portfolio PDF exporter using Playwright.
HTML is rendered via Jinja2 then converted to A4 PDF via Playwright/Chromium.
"""
import logging
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.teacher import Teacher
from app.models.portfolio_export import PortfolioExport
from app.services.evidences import get_teacher_evidences, ALLOWED_CATEGORIES

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
_jinja_env = Environment(loader=FileSystemLoader(str(_TEMPLATES_DIR)), autoescape=True)


def _academic_year() -> str:
    now = datetime.now()
    if now.month >= 9:
        return f"{now.year}/{now.year + 1}"
    return f"{now.year - 1}/{now.year}"


def _build_categories(evidences: list) -> list[dict]:
    """Group evidences by category in the canonical order."""
    grouped: dict[str, list] = {cat: [] for cat in ALLOWED_CATEGORIES}
    for ev in evidences:
        cat = ev.category or "أخرى"
        if cat not in grouped:
            cat = "أخرى"
        grouped[cat].append(ev)

    result = []
    for name in ALLOWED_CATEGORIES:
        items = grouped[name]
        result.append({"name": name, "evidences": items, "count": len(items)})
    return result


def _render_html(teacher: Teacher, evidences: list) -> str:
    categories = _build_categories(evidences)
    template = _jinja_env.get_template("portfolio.html")
    return template.render(
        teacher=teacher,
        categories=categories,
        total_count=len(evidences),
        academic_year=_academic_year(),
        generated_at=datetime.now().strftime("%Y/%m/%d %H:%M"),
    )


async def _generate_pdf(html: str, output_path: Path) -> None:
    """Render HTML to A4 PDF via Playwright Chromium."""
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        page = await browser.new_page()
        await page.set_content(html, wait_until="networkidle")

        await page.pdf(
            path=str(output_path),
            format="A4",
            print_background=True,
            margin={"top": "18mm", "bottom": "18mm", "left": "15mm", "right": "15mm"},
        )
        await browser.close()


def build_portfolio_json(db: Session, teacher_id: int) -> dict:
    """
    Build a structured JSON representation of the teacher's portfolio.
    Used as the canonical data source for both the PDF exporter and any
    future API/preview endpoints.

    Safety: SELECT is always WHERE teacher_id = :teacher_id.
    """
    from app.services.teachers import get_teacher_by_id

    teacher = get_teacher_by_id(db, teacher_id)
    if not teacher:
        raise ValueError(f"Teacher {teacher_id} not found")

    evidences = get_teacher_evidences(db, teacher_id, limit=1000)

    # Group into canonical ordered sections
    grouped: dict[str, list] = {cat: [] for cat in ALLOWED_CATEGORIES}
    for ev in evidences:
        cat = ev.category or "أخرى"
        if cat not in grouped:
            cat = "أخرى"
        grouped[cat].append({
            "id": ev.id,
            "title": ev.title or "",
            "description": ev.description or "",
            "grade": ev.grade or "",
            "subject": ev.subject or "",
            "evidence_type": ev.evidence_type,
            "category": cat,
            "message_text": ev.message_text or "",
            "storage_path": ev.storage_path or "",
            "file_name": ev.file_name or "",
            "media_url": ev.media_url or "",
            "ai_status": ev.ai_status,
            "created_at": ev.created_at.isoformat() if ev.created_at else "",
        })

    sections = [
        {"category": cat, "items": items}
        for cat, items in grouped.items()
        if items
    ]

    return {
        "teacher": {
            "id": teacher.id,
            "phone": teacher.phone,
            "name": teacher.name or "",
            "subject": teacher.subject or "",
            "stage": teacher.stage or "",
            "grades": teacher.grades or "",
            "school_name": teacher.school_name or "",
            "principal_name": teacher.principal_name or "",
        },
        "academic_year": _academic_year(),
        "total_count": len(evidences),
        "sections": sections,
    }


def create_export_record(db: Session, teacher_id: int) -> PortfolioExport:
    record = PortfolioExport(teacher_id=teacher_id, status="pending")
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


async def run_export_background(teacher_id: int, export_id: int) -> None:
    """
    Background task: generate PDF for teacher and update the export record.
    Opens its own DB session so it isn't affected by the closed request session.
    Each teacher's files are isolated under their own directory.
    """
    from app.db.base import SessionLocal
    from app.services.teachers import get_teacher_by_id

    db: Session = SessionLocal()
    try:
        record = db.query(PortfolioExport).filter(PortfolioExport.id == export_id).first()
        if not record:
            return

        record.status = "processing"
        db.commit()

        # Re-fetch teacher inside fresh session
        teacher = get_teacher_by_id(db, teacher_id)
        if not teacher:
            record.status = "error"
            record.error = f"Teacher {teacher_id} not found"
            db.commit()
            return

        evidences = get_teacher_evidences(db, teacher_id, limit=1000)
        html = _render_html(teacher, evidences)

        export_dir = settings.export_storage(teacher_id)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"shawahid_teacher_{teacher_id}_{timestamp}.pdf"
        output_path = export_dir / filename

        await _generate_pdf(html, output_path)

        pdf_url = f"{settings.effective_base_url}/files/teachers/{teacher_id}/exports/{filename}"
        record.storage_path = str(output_path)
        record.pdf_url = pdf_url
        record.status = "done"
        db.commit()

        logger.info("Export done for teacher %d: %s", teacher_id, pdf_url)

    except Exception as exc:
        logger.error("Export failed for teacher %d: %s", teacher_id, exc)
        try:
            record = db.query(PortfolioExport).filter(PortfolioExport.id == export_id).first()
            if record:
                record.status = "error"
                record.error = str(exc)
                db.commit()
        except Exception:
            pass
    finally:
        db.close()
