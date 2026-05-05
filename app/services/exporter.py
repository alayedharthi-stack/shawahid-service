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


# Category metadata: English name, icon, Arabic description, educational value (per-card)
_CATEGORY_META: dict[str, dict] = {
    "نشاط صفي":                    {"en": "Classroom Activity",     "icon": "🏫", "desc": "يوثق هذا المحور الأنشطة الصفية المتنوعة التي تُنفَّذ داخل الفصل الدراسي وتُسهم في تفعيل بيئة التعلم.",         "value": "يعكس هذا الشاهد نشاطًا تعليميًا فعّالًا يُسهم في تحقيق أهداف المنهج وتنمية مهارات الطلاب."},
    "تعلم تعاوني":                  {"en": "Cooperative Learning",    "icon": "🤝", "desc": "يوثق هذا المحور تطبيق استراتيجيات العمل الجماعي وتعاون الطلاب في تحقيق أهداف التعلم المشتركة.",         "value": "يُعزز هذا الشاهد مهارات التعاون والعمل الجماعي ويُنمّي التواصل الإيجابي بين الطلاب."},
    "حل تمارين":                    {"en": "Exercise Solving",         "icon": "✏️", "desc": "يوثق هذا المحور مشاركة الطلاب في حل التمارين والمسائل، مما يعكس مستوى الفهم والتطبيق.",             "value": "يُظهر هذا الشاهد قدرة الطلاب على التطبيق العملي للمفاهيم ويكشف مستوى استيعابهم."},
    "مشاركة طلابية":                {"en": "Student Participation",   "icon": "🙋", "desc": "يوثق هذا المحور مستوى تفاعل الطلاب ومشاركتهم الفعّالة داخل الحصة الدراسية.",                        "value": "يدل هذا الشاهد على بيئة صفية تفاعلية تُحفز المشاركة وتُعزز روح الانتماء التعليمي."},
    "تكريم وتميز":                  {"en": "Motivation & Recognition", "icon": "🏆", "desc": "يوثق هذا المحور جهود تحفيز المتميزين وتعزيز ثقافة التميز والمنافسة الإيجابية بين الطلاب.",          "value": "يُجسّد هذا الشاهد استراتيجية التعزيز الإيجابي ودوره في رفع الدافعية والإنجاز."},
    "شرح درس":                      {"en": "Lesson Delivery",          "icon": "📖", "desc": "يوثق هذا المحور أساليب شرح الدروس وتوصيل المفاهيم الأساسية للطلاب بصورة واضحة وفعّالة.",           "value": "يُبرز هذا الشاهد جودة التوصيل والشرح ويُثبت امتلاك المعلم للمحتوى وأساليب التبسيط."},
    "واجب منزلي":                   {"en": "Homework",                 "icon": "📝", "desc": "يوثق هذا المحور الواجبات المنزلية المقدمة للطلاب ومدى استمرارية التعلم خارج الفصل.",               "value": "يُؤكد هذا الشاهد على استمرارية التعلم وربط الفصل بالبيت لتعزيز الفهم."},
    "اختبار":                       {"en": "Assessment",               "icon": "📋", "desc": "يوثق هذا المحور الاختبارات الرسمية والمهام الأدائية المُعدَّة لقياس نواتج تعلم الطلاب.",            "value": "يُعبّر هذا الشاهد عن ممارسة تقويمية منهجية تقيس الفهم وتُغذّي قرارات التدريس."},
    "ورقة عمل":                     {"en": "Worksheet",                "icon": "📄", "desc": "يوثق هذا المحور أوراق العمل والأنشطة الكتابية التي أعدّها المعلم لتعزيز الفهم والتطبيق.",           "value": "يُجسّد هذا الشاهد الإعداد المسبق والتخطيط الجيد لضمان تنويع أدوات التعلم."},
    "تقويم":                        {"en": "Evaluation",               "icon": "📊", "desc": "يوثق هذا المحور ممارسات التقويم المستمر وأدوات رصد مستوى الطلاب طوال الفصل الدراسي.",             "value": "يُدل هذا الشاهد على الاهتمام بالتغذية الراجعة والتقويم المستمر لدعم الطالب."},
    "مصدر تعليمي":                  {"en": "Educational Resource",     "icon": "🎯", "desc": "يوثق هذا المحور المصادر والمواد التعليمية المتنوعة التي وظّفها المعلم لإثراء العملية التعليمية.",     "value": "يُعكس هذا الشاهد وعي المعلم بتنويع مصادر التعلم والاستفادة من الموارد التعليمية."},
    "رابط إثرائي":                  {"en": "Enrichment Link",          "icon": "🔗", "desc": "يوثق هذا المحور الروابط والمحتوى الرقمي الإثرائي المُشارَك مع الطلاب لتعميق الفهم.",               "value": "يُجسّد هذا الشاهد توظيف التقنية وربط التعلم بمصادر رقمية معيارية وموثوقة."},
    "تواصل مع أولياء الأمور":       {"en": "Parent Communication",    "icon": "👨‍👩‍👧", "desc": "يوثق هذا المحور قنوات التواصل الفعّال مع أولياء أمور الطلاب وتعزيز الشراكة المجتمعية.",         "value": "يُثبت هذا الشاهد أن المعلم يُشرك الأسرة في العملية التعليمية ويُرسّخ ثقافة الشراكة."},
    "ملف إداري":                    {"en": "Administrative File",      "icon": "🗂️", "desc": "يوثق هذا المحور الوثائق والملفات الإدارية ذات الصلة بالعمل المدرسي اليومي.",                        "value": "يُبيّن هذا الشاهد التزام المعلم بالمتطلبات الإدارية ودقة التوثيق المهني."},
    "إنجاز طلابي":                  {"en": "Student Achievement",      "icon": "⭐", "desc": "يوثق هذا المحور إنجازات الطلاب ومخرجاتهم التعليمية المتميزة خلال الفصل الدراسي.",                   "value": "يُعكس هذا الشاهد أثر التدريس الفعّال على مخرجات الطلاب وتطور أدائهم."},
    # Legacy (backward compat)
    "التخطيط":                      {"en": "Planning",                 "icon": "📅", "desc": "يوثق هذا المحور عناية المعلم بالتخطيط المسبق وتوزيع المنهج والخطط التعليمية الأسبوعية.",            "value": "يُبرز هذا الشاهد الكفاءة في التخطيط ويُثبت الاستعداد والاحترافية قبل الدرس."},
    "التنفيذ داخل الصف":            {"en": "Classroom Implementation", "icon": "🖥️", "desc": "يوثق هذا المحور تنفيذ الدروس داخل الصف باستخدام الأساليب والتقنيات الحديثة.",                       "value": "يُجسّد هذا الشاهد الكفاءة في التنفيذ وحسن استخدام الوقت والأدوات التعليمية."},
    "التعلم التعاوني":              {"en": "Cooperative Learning",     "icon": "🤝", "desc": "يوثق هذا المحور تطبيق العمل الجماعي بين الطلاب وتبادل الخبرات التعليمية.",                          "value": "يُعزز هذا الشاهد مهارات التعاون والعمل الجماعي بين الطلاب."},
    "التعلم بالممارسة":             {"en": "Learning by Doing",        "icon": "🔬", "desc": "يوثق هذا المحور التطبيق العملي للمفاهيم وتحويل التعلم من النظري إلى العملي.",                        "value": "يُرسّخ هذا الشاهد التعلم النشط ويُحوّل المعرفة النظرية إلى تطبيق ملموس."},
    "التقويم":                      {"en": "Assessment & Evaluation",  "icon": "📊", "desc": "يوثق هذا المحور تنويع أدوات التقويم ودعم قياس نواتج التعلم.",                                        "value": "يُمثل هذا الشاهد ممارسة تقويمية سليمة تقيس أثر التدريس وتُغذّي التطوير."},
    "التحفيز":                      {"en": "Motivation",               "icon": "🏆", "desc": "يوثق هذا المحور أساليب تحفيز الطلاب وتكريم المتميزين.",                                             "value": "يُعزز هذا الشاهد الدافعية لدى الطلاب ويبني جو تعليمي إيجابي محفز."},
    "سجل المتابعة":                  {"en": "Follow-up Log",            "icon": "📋", "desc": "يوثق هذا المحور سجلات متابعة الطلاب اليومية والمتابعة المنتظمة للأداء.",                             "value": "يُثبت هذا الشاهد الدقة في المتابعة والاهتمام بكل طالب على حدة."},
    "الدورات والشهادات":             {"en": "Courses & Certificates",   "icon": "🎓", "desc": "يوثق هذا المحور الدورات التدريبية والشهادات المهنية التي حصل عليها المعلم.",                         "value": "يُدل هذا الشاهد على سعي المعلم للتطوير المهني المستمر ورفع مستوى الكفاءة."},
    "المبادرات والأنشطة":            {"en": "Initiatives & Activities", "icon": "💡", "desc": "يوثق هذا المحور المبادرات الإبداعية والأنشطة المدرسية المتنوعة.",                                   "value": "يُجسّد هذا الشاهد روح المبادرة والقيادة التربوية خارج حدود الفصل الدراسي."},
    "أخرى":                         {"en": "Other",                    "icon": "📌", "desc": "شواهد متنوعة لا تنتمي لتصنيف محدد.",                                                                  "value": "يُوثّق هذا الشاهد جهدًا تعليميًا متنوعًا يُثري مسيرة المعلم المهنية."},
}

_DEFAULT_META = {"en": "", "icon": "📌", "desc": "", "value": ""}


def _build_categories(evidences: list) -> list[dict]:
    """
    Group evidences by category, ordered by count (desc).
    Categories with zero evidences are excluded from the PDF.
    Old and new categories are both handled.
    """
    grouped: dict[str, list] = {}
    for ev in evidences:
        cat = (ev.category or "أخرى").strip()
        grouped.setdefault(cat, []).append(ev)

    # Order: defined categories first (in canonical order), then any others
    order = list(ALLOWED_CATEGORIES) + [c for c in grouped if c not in ALLOWED_CATEGORIES]
    result = []
    for name in order:
        items = grouped.get(name, [])
        meta = _CATEGORY_META.get(name, _DEFAULT_META)
        result.append({
            "name":      name,
            "en":        meta["en"],
            "icon":      meta.get("icon", "📌"),
            "desc":      meta.get("desc", ""),
            "value":     meta.get("value", ""),
            "evidences": items,
            "count":     len(items),
        })
    return result


def _build_stats(evidences: list, categories: list[dict]) -> dict:
    """Build statistics dict for the summary page."""
    counts = {"images": 0, "videos": 0, "audios": 0, "documents": 0, "urls": 0, "texts": 0}
    for ev in evidences:
        t = (ev.evidence_type or "text").lower()
        if t == "image":            counts["images"] += 1
        elif t == "video":          counts["videos"] += 1
        elif t == "audio":          counts["audios"] += 1
        elif t in ("pdf", "document"): counts["documents"] += 1
        elif t == "url":            counts["urls"] += 1
        else:                       counts["texts"] += 1

    # Top 5 non-empty categories (for bar chart)
    nonempty = sorted([c for c in categories if c["count"] > 0],
                      key=lambda c: c["count"], reverse=True)[:5]
    max_count = nonempty[0]["count"] if nonempty else 1
    top_categories = [
        {"name": c["name"], "count": c["count"], "pct": round(c["count"] / max_count * 100)}
        for c in nonempty
    ]

    return {**counts, "top_categories": top_categories}


def _render_html(teacher: Teacher, evidences: list) -> str:
    categories = _build_categories(evidences)
    stats      = _build_stats(evidences, categories)
    template   = _jinja_env.get_template("portfolio.html")
    return template.render(
        teacher=teacher,
        categories=categories,
        stats=stats,
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
    Background task: generate PDF → update record → send download link via WhatsApp.
    Opens its own DB session (avoids DetachedInstanceError).
    Sends WhatsApp message to teacher on success AND on failure — never silent.
    """
    from app.db.base import SessionLocal
    from app.services.teachers import get_teacher_by_id
    from app.services.whatsapp import send_whatsapp_message

    db: Session = SessionLocal()
    teacher_phone: str | None = None

    try:
        record = db.query(PortfolioExport).filter(PortfolioExport.id == export_id).first()
        if not record:
            logger.error("[EXPORT FAILED] export_id=%d not found in DB", export_id)
            return

        record.status = "processing"
        db.commit()

        teacher = get_teacher_by_id(db, teacher_id)
        if not teacher:
            logger.error("[EXPORT FAILED] teacher_id=%d not found", teacher_id)
            record.status = "error"
            record.error = f"Teacher {teacher_id} not found"
            db.commit()
            return

        teacher_phone = teacher.phone
        teacher_name  = teacher.name or "أستاذ"
        evidences = get_teacher_evidences(db, teacher_id, limit=1000)

        logger.info(
            "[EXPORT STARTED] teacher_id=%d evidence_count=%d export_id=%d",
            teacher_id, len(evidences), export_id,
        )

        html = _render_html(teacher, evidences)

        export_dir = settings.export_storage(teacher_id)
        timestamp  = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename   = f"shawahid_teacher_{teacher_id}_{timestamp}.pdf"
        output_path = export_dir / filename

        await _generate_pdf(html, output_path)
        logger.info("[PDF GENERATED] path=%s", output_path)

        pdf_url = (
            f"{settings.effective_base_url}/files"
            f"/teachers/{teacher_id}/exports/{filename}"
        )
        record.storage_path = str(output_path)
        record.pdf_url      = pdf_url
        record.status       = "done"
        db.commit()

        logger.info("[PDF URL CREATED] teacher_id=%d url=%s", teacher_id, pdf_url)

        # ── Send download link to teacher via WhatsApp ────────────────────────
        success_msg = (
            f"✅ تم إنشاء ملف شواهدك بنجاح يا {teacher_name}!\n\n"
            f"رابط التحميل:\n{pdf_url}"
        )
        sent = await send_whatsapp_message(
            teacher_phone, success_msg, teacher_id=teacher_id, context="export_done"
        )
        if sent:
            logger.info("[PDF SEND SUCCESS] teacher_id=%d", teacher_id)

            # ── Post-export upsell nudge (one message, sent immediately after link) ──
            upsell_msg = (
                "قريبًا سأساعدك أيضًا في إعداد أوراق عمل واختبارات "
                "من نفس شواهدك 📚✨"
            )
            try:
                await send_whatsapp_message(
                    teacher_phone, upsell_msg,
                    teacher_id=teacher_id, context="export_upsell"
                )
                logger.info("[PDF UPSELL SENT] teacher_id=%d", teacher_id)
            except Exception as upsell_exc:
                logger.warning(
                    "[PDF UPSELL FAILED] teacher_id=%d: %s", teacher_id, upsell_exc
                )
        else:
            logger.warning(
                "[PDF SEND FAILED] WhatsApp delivery failed for teacher_id=%d url=%s",
                teacher_id, pdf_url,
            )

    except Exception as exc:
        logger.error("[EXPORT FAILED] teacher_id=%d error=%s", teacher_id, exc, exc_info=True)
        try:
            record = db.query(PortfolioExport).filter(PortfolioExport.id == export_id).first()
            if record:
                record.status = "error"
                record.error  = str(exc)
                db.commit()
        except Exception:
            pass

        # Notify teacher about the failure (never silent)
        if teacher_phone:
            try:
                from app.services.whatsapp import send_whatsapp_message as _send
                fail_msg = (
                    "تعذّر إنشاء ملف الشواهد الآن 🙏 "
                    "سنحاول مرة أخرى. "
                    "تواصل مع الدعم إن تكررت المشكلة."
                )
                await _send(teacher_phone, fail_msg, teacher_id=teacher_id, context="export_failed")
            except Exception as wa_exc:
                logger.error("Could not notify teacher of export failure: %s", wa_exc)
    finally:
        db.close()
