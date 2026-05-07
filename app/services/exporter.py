"""
Portfolio PDF exporter using Playwright.
HTML is rendered via Jinja2 then converted to A4 PDF via Playwright/Chromium.
"""
import base64
import logging
import mimetypes
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

_CATEGORY_META.update({
    "التعلم النشط": {
        "en": "Active Learning", "icon": "🤝",
        "desc": "يوثق هذا المحور ممارسات التعلم النشط ومشاركة الطلاب في بناء المعرفة وتطبيقها.",
        "value": "يعكس هذا الشاهد توظيف استراتيجيات تعلم فاعلة تزيد مشاركة الطلاب ومسؤوليتهم عن التعلم.",
    },
    "التعلم التعاوني": {
        "en": "Cooperative Learning", "icon": "🤝",
        "desc": "يوثق هذا المحور تطبيق استراتيجيات العمل الجماعي وتعاون الطلاب في تحقيق أهداف التعلم المشتركة.",
        "value": "يُعزز هذا الشاهد مهارات التعاون والعمل الجماعي ويُنمّي التواصل الإيجابي بين الطلاب.",
    },
    "التعلم بالممارسة": {
        "en": "Learning by Doing", "icon": "🔬",
        "desc": "يوثق هذا المحور التطبيق العملي للمفاهيم وتحويل التعلم من النظري إلى الميداني.",
        "value": "يُرسّخ هذا الشاهد التعلم النشط ويُحوّل المعرفة النظرية إلى تطبيق ملموس.",
    },
    "إدارة الصف": {
        "en": "Classroom Management", "icon": "🏫",
        "desc": "يوثق هذا المحور ممارسات تنظيم البيئة الصفية وإدارة التفاعل داخل الحصة.",
        "value": "يدل هذا الشاهد على قدرة المعلم على بناء بيئة تعلم منظمة ومحفزة.",
    },
    "التواصل": {
        "en": "Communication", "icon": "💬",
        "desc": "يوثق هذا المحور تواصل المعلم مع الطلاب وأولياء الأمور والشركاء في العملية التعليمية.",
        "value": "يبرز هذا الشاهد أثر التواصل المهني في دعم الطالب وتعزيز الشراكة التعليمية.",
    },
    "مصادر تعليمية": {
        "en": "Learning Resources", "icon": "📚",
        "desc": "يوثق هذا المحور المصادر والمواد التعليمية الرقمية وغير الرقمية التي وظّفها المعلم.",
        "value": "يجسد هذا الشاهد حسن توظيف الموارد التعليمية والتقنية لتنويع خبرات التعلم.",
    },
    "ملفات إدارية": {
        "en": "Administrative Files", "icon": "🗂️",
        "desc": "يوثق هذا المحور الملفات والوثائق الإدارية الرسمية المرتبطة بالعمل المدرسي.",
        "value": "يُبيّن هذا الشاهد التزام المعلم بالمتطلبات الإدارية ودقة التوثيق والتنظيم المهني.",
    },
    "روابط إثرائية": {
        "en": "Enrichment Links", "icon": "🔗",
        "desc": "يوثق هذا المحور الروابط والمحتوى الرقمي الإثرائي المُشارَك مع الطلاب لتعميق الفهم.",
        "value": "يُجسّد هذا الشاهد توظيف التقنية وربط التعلم بمصادر رقمية معيارية وموثوقة.",
    },
    # backward-compat alias — نادر الظهور بعد إعادة التعيين
    "ملفات واختبارات": {
        "en": "Files & Assessments", "icon": "📄",
        "desc": "يوثق هذا المحور الاختبارات وأوراق العمل والملفات التعليمية.",
        "value": "يثبت هذا الشاهد عناية المعلم بإعداد أدوات تعليمية وتقويمية منظمة.",
    },
})

_MAIN_CATEGORY_ORDER = [
    "التخطيط",
    "سجل المتابعة",        # تنظيمي أساسي — يأتي مبكرًا بعد التخطيط
    "التنفيذ داخل الصف",
    "التعلم النشط",
    "التعلم التعاوني",     # محور مستقل (لم يعد sub لـ التعلم النشط)
    "التعلم بالممارسة",    # محور مستقل
    "التقويم",
    "التحفيز",
    "إدارة الصف",
    "التواصل",
    "مصادر تعليمية",
    "ملفات إدارية",        # كان: ملفات واختبارات
    "روابط إثرائية",       # محور مستقل (لم يعد sub لـ مصادر تعليمية)
    "أخرى",
]

_SUB_TO_MAIN_CATEGORY = {
    # ── التنفيذ ───────────────────────────────────────────────────────
    "نشاط صفي":                "التنفيذ داخل الصف",
    "شرح درس":                 "التنفيذ داخل الصف",
    "حل تمارين":               "التنفيذ داخل الصف",
    "المبادرات والأنشطة":       "التنفيذ داخل الصف",
    # ── التعلم النشط (مشاركة عامة — تبقى هنا) ───────────────────────
    "مشاركة طلابية":           "التعلم النشط",
    "إنجاز طلابي":             "التعلم النشط",
    # ── التعلم التعاوني (محور مستقل الآن) ───────────────────────────
    "تعلم تعاوني":             "التعلم التعاوني",   # كان → التعلم النشط
    # "التعلم التعاوني" يُعيَّن لنفسه (محور رئيسي) — لا حاجة لإدراجه
    # ── التعلم بالممارسة (محور مستقل الآن) ──────────────────────────
    # "التعلم بالممارسة" يُعيَّن لنفسه (محور رئيسي) — لا حاجة لإدراجه
    # ── التقويم ──────────────────────────────────────────────────────
    "تقويم":                   "التقويم",
    "التقويم":                 "التقويم",
    "واجب منزلي":              "التقويم",
    "اختبار":                  "التقويم",          # كان → ملفات واختبارات
    "ورقة عمل":                "التقويم",          # كان → ملفات واختبارات
    # ── التحفيز ──────────────────────────────────────────────────────
    "تكريم وتميز":             "التحفيز",
    "التحفيز":                 "التحفيز",
    # ── سجل المتابعة ─────────────────────────────────────────────────
    "سجل المتابعة":            "سجل المتابعة",
    # ── ملفات إدارية (محور مستقل — كان: سجل المتابعة) ───────────────
    "ملف إداري":               "ملفات إدارية",     # كان → سجل المتابعة
    "ملفات واختبارات":          "ملفات إدارية",     # إعادة تعيين القديم
    # ── التواصل ──────────────────────────────────────────────────────
    "تواصل مع أولياء الأمور":  "التواصل",
    # ── مصادر تعليمية ────────────────────────────────────────────────
    "مصدر تعليمي":             "مصادر تعليمية",
    "مصادر تعليمية":           "مصادر تعليمية",
    # ── روابط إثرائية (محور مستقل الآن) ─────────────────────────────
    "رابط إثرائي":             "روابط إثرائية",    # كان → مصادر تعليمية
    # ── التخطيط ──────────────────────────────────────────────────────
    "الدورات والشهادات":        "التخطيط",
}

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
_SUPPORT_WHATSAPP = "966544761054"
_NULLISH_TEXT = {"", "null", "none", "undefined", "nan"}


def _clean_text(value) -> str | None:
    """Return clean display text, treating DB/null placeholders as absent."""
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in _NULLISH_TEXT:
        return None
    return text


_ENRICHED_SECTION_LABELS = (
    "وصف الشاهد",
    "الهدف التربوي",
    "الأثر على الطلاب",
    "تأمل المعلم",
    "الارتباط بالمعايير",
)


def _parse_enriched_sections(value: str | None) -> list[dict[str, str]]:
    text = _clean_text(value)
    if not text:
        return []

    sections: list[dict[str, str]] = []
    current_label: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_label, current_lines
        if not current_label:
            return
        content = " ".join(line.strip() for line in current_lines if line.strip()).strip()
        if content and not any(item["label"] == current_label and item["text"] == content for item in sections):
            sections.append({"label": current_label, "text": content})
        current_label = None
        current_lines = []

    for raw_line in text.splitlines():
        line = raw_line.strip().lstrip("-•0123456789). ").strip()
        matched = next((label for label in _ENRICHED_SECTION_LABELS if line.startswith(label)), None)
        if matched:
            flush()
            current_label = matched
            current_lines = [line[len(matched):].lstrip(":：- ").strip()]
        elif current_label:
            current_lines.append(line)

    flush()
    return sections


def _has_meaningful_text(value, *, min_chars: int = 4) -> bool:
    text = _clean_text(value)
    if not text:
        return False
    compact = "".join(ch for ch in text if ch.isalnum())
    return len(compact) >= min_chars


def _looks_malformed_text(value) -> bool:
    text = _clean_text(value)
    if not text:
        return False
    question_marks = text.count("?") + text.count("؟")
    if question_marks >= 3 and question_marks >= len(text.strip()) // 2:
        return True
    return not any(ch.isalnum() for ch in text)


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    haystack = text.lower()
    return any(keyword in haystack for keyword in keywords)


# ── Instruction/system-command detection ──────────────────────────────────────
# These phrases indicate the teacher was giving a system command, updating their
# profile, or asking a question — NOT documenting evidence. Records matching
# these patterns should be excluded from the PDF export (not deleted from DB).
_INSTRUCTION_KEYWORDS: tuple[str, ...] = (
    "حدث بياناتي",
    "حدث بيانات",
    "غير المادة",
    "اكتب في الملف",
    "عدل القالب",
    "ماذا فهمت",
    "هذا ليس شاهد",
    "لا تحفظه",
    "أضف بيانات",
    "اضف بيانات",
    "اسمي ",
    "مدرستي ",
    "مادتي ",
    "صفوفي ",
    "تحديث البيانات",
    "غيّر البيانات",
    "غير بياناتي",
    "غير اسمي",
    "غير مدرستي",
    "غير مادتي",
    "بياناتي الشخصية",
    "سجل بياناتي",
    "ادخل بياناتي",
    "احفظ بياناتي",
    "اكتب اسمي",
    "تعديل البيانات",
    "تعديل بياناتي",
)

# Minimum content length for an audio/voice evidence to be exported.
# Very short audios without any instructional text are likely noise.
_AUDIO_MIN_TEXT_CHARS = 20


def is_instruction_evidence(ev_dict: dict) -> bool:
    """Return True if this evidence looks like a system instruction, not a real evidence."""
    ev_type = (ev_dict.get("evidence_type") or "").lower()
    # Only apply instruction filtering to audio, voice, and text evidence.
    if ev_type not in ("audio", "voice", "text"):
        return False
    text_parts = [
        ev_dict.get("title") or "",
        ev_dict.get("description") or "",
        ev_dict.get("message_text") or "",
    ]
    combined = " ".join(p for p in text_parts if p).strip()
    if not combined:
        return False
    return _contains_any(combined, _INSTRUCTION_KEYWORDS)


_PLANNING_KEYWORDS = (
    "خطة المعلم",
    "الخطة الدراسية",
    "خطة دراسية",
    "توزيع منهج",
    "التخطيط",
)
_FOLLOW_UP_KEYWORDS = (
    "سجل متابعة",
    "سجل المتابعة",
    "متابعة الطالب",
    "سجل الطالب",
    "لوحة الأداء",
)


def _official_category_from_content(category: str, *parts: str | None) -> str:
    """Promote official planning/follow-up documents to their expected axes."""
    text = " ".join(part for part in parts if part)
    if _contains_any(text, _PLANNING_KEYWORDS):
        return "التخطيط"
    if _contains_any(text, _FOLLOW_UP_KEYWORDS):
        return "سجل المتابعة"
    return category


def _friendly_file_label(file_name: str | None, evidence_type: str, category: str) -> str:
    name = _clean_text(file_name)
    suffix = Path(name).suffix.lower() if name else ""
    lower_name = name.lower() if name else ""
    is_technical = (
        not name
        or lower_name.startswith(("file_", "document_", "media_"))
        or len(Path(name).stem) > 32
    )

    if suffix == ".pdf" or evidence_type == "pdf":
        return "ملف PDF مرفق" if is_technical else name
    if category == "ملفات إدارية":
        return "ملف إداري مرفق" if is_technical else name
    if evidence_type == "document":
        return "وثيقة تعليمية" if is_technical else name
    return "ملف مرفق" if is_technical else name


def _link_type_label(category: str, title: str, message_text: str | None) -> str:
    text = f"{title} {message_text or ''}"
    if "فيديو" in text or "youtube" in text.lower() or "youtu.be" in text.lower():
        return "فيديو"
    if "مصدر" in text or category == "مصادر تعليمية":
        return "مصدر"
    return "رابط إثرائي"


def _safe_link_href(media_url: str | None, message_text: str | None) -> str | None:
    for value in (media_url, message_text):
        text = _clean_text(value)
        if text and text.lower().startswith(("http://", "https://")) and _is_safe_public_url(text):
            return text
    return None


def _is_safe_public_url(url: str | None) -> bool:
    text = _clean_text(url)
    if not text:
        return False
    lowered = text.lower()
    # These hosts serve temporary/auth-gated media that will return 401 for end users
    blocked_hosts = (
        "lookaside.fbsbx.com",
        "lookaside.facebook.com",
        "fbsbx.com",
        "fbcdn.net",
        "facebook.com",
        "graph.facebook.com",
        "whatsapp.net",
        "whatsapp.com",
        "mmg.whatsapp.net",
        "media.whatsapp.net",
    )
    return lowered.startswith(("http://", "https://")) and not any(host in lowered for host in blocked_hosts)


def _public_storage_url(path: str | None) -> str | None:
    """Build a stable public /files URL for files stored under STORAGE_ROOT."""
    clean_path = _clean_text(path)
    if not clean_path:
        return None

    file_path = Path(clean_path)
    try:
        rel_path = file_path.resolve().relative_to(settings.storage_path.resolve())
    except Exception:
        return None

    return f"{settings.effective_base_url}/files/{rel_path.as_posix()}"


def _public_media_url(evidence_type: str, storage_path: str | None, media_url: str | None) -> str | None:
    """Prefer long-lived service-hosted links; never expose temporary CDN URLs."""
    local_url = _public_storage_url(storage_path)
    if local_url:
        suffix = Path(storage_path or "").suffix.lower()
        if evidence_type == "video" and suffix in _IMAGE_EXTS:
            return None
        return local_url
    return media_url if _is_safe_public_url(media_url) else None


def _file_type_label(file_name: str | None, evidence_type: str, mime_type: str | None) -> str:
    name = _clean_text(file_name) or ""
    suffix = Path(name).suffix.lower()
    mime = (_clean_text(mime_type) or "").lower()
    if suffix == ".pdf" or evidence_type == "pdf" or "pdf" in mime:
        return "PDF"
    if suffix in {".doc", ".docx"} or "word" in mime:
        return "مستند"
    if suffix in _IMAGE_EXTS or mime.startswith("image/"):
        return "صورة"
    if suffix in {".ppt", ".pptx"}:
        return "عرض تقديمي"
    return "ملف"


def _ministry_logo_svg_data_uri() -> str:
    """Full white ministry crest (icon + Arabic name + English name) embedded
    as a single SVG, so the cover renders it as ONE integrated watermark
    instead of an icon plus separate text labels.

    All elements share the same fill (#ffffff) and a single opacity stop,
    so the whole mark reads as one coherent monochrome official identity.
    Rendered inline by Chromium during Playwright PDF export — no external
    fonts required (Chromium falls back to its bundled Arabic faces, which
    display "وزارة التعليم" correctly).
    """
    svg = """
<svg xmlns="http://www.w3.org/2000/svg"
     width="220" height="180" viewBox="0 0 220 180">
  <g fill="#ffffff" opacity="0.95">
    <!-- ═══ Left dot cluster (3 rows × 5 dots, descending size) ═══ -->
    <circle cx="34" cy="14" r="6.8"/>
    <circle cx="51" cy="14" r="6.8"/>
    <circle cx="68" cy="15" r="6.5"/>
    <circle cx="85" cy="18" r="5.6"/>
    <circle cx="100" cy="24" r="4.6"/>
    <circle cx="34" cy="33" r="6.6"/>
    <circle cx="51" cy="33" r="6.2"/>
    <circle cx="68" cy="35" r="5.6"/>
    <circle cx="85" cy="40" r="4.7"/>
    <circle cx="100" cy="46" r="3.9"/>
    <circle cx="34" cy="52" r="6.2"/>
    <circle cx="51" cy="52" r="5.6"/>
    <circle cx="68" cy="54" r="5.0"/>
    <circle cx="85" cy="59" r="4.0"/>
    <circle cx="98" cy="64" r="3.2"/>

    <!-- ═══ Right dot cluster (mirror, ascending size outward) ═══ -->
    <circle cx="120" cy="24" r="4.6"/>
    <circle cx="135" cy="18" r="5.6"/>
    <circle cx="152" cy="15" r="6.5"/>
    <circle cx="169" cy="14" r="6.8"/>
    <circle cx="186" cy="14" r="6.8"/>
    <circle cx="120" cy="46" r="3.9"/>
    <circle cx="135" cy="40" r="4.7"/>
    <circle cx="152" cy="35" r="5.6"/>
    <circle cx="169" cy="33" r="6.2"/>
    <circle cx="186" cy="33" r="6.6"/>
    <circle cx="122" cy="64" r="3.2"/>
    <circle cx="135" cy="59" r="4.0"/>
    <circle cx="152" cy="54" r="5.0"/>
    <circle cx="169" cy="52" r="5.6"/>
    <circle cx="186" cy="52" r="6.2"/>
  </g>

  <!-- Arabic: وزارة التعليم — primary identity line -->
  <text x="110" y="115"
        text-anchor="middle"
        font-family="'Cairo','Tajawal','IBM Plex Sans Arabic','Tahoma','Arial',sans-serif"
        font-size="26"
        font-weight="700"
        fill="#ffffff"
        opacity="0.95"
        direction="rtl"
        xml:lang="ar">وزارة التعليم</text>

  <!-- English: Ministry of Education — secondary line -->
  <text x="110" y="145"
        text-anchor="middle"
        font-family="'Cairo','Tahoma','Arial','Segoe UI',sans-serif"
        font-size="13.5"
        font-weight="500"
        fill="#ffffff"
        opacity="0.78"
        letter-spacing="0.4">Ministry of Education</text>
</svg>
""".strip()
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


# ── Arabic-aware subject formatter ────────────────────────────────────────────
# Used by the cover headline ("ملف الشواهد لمعلم {subject_with_al}") so the
# Arabic grammar reads correctly: "لمعلم الرياضيات" / "لمعلم الدراسات
# الاجتماعية" / "لمعلم اللغة العربية" — never "لمعلم رياضيات".
_SUBJECT_WITH_AL_OVERRIDES: dict[str, str] = {
    # Core subjects (exact name → properly-grammared form)
    "رياضيات":               "الرياضيات",
    "علوم":                  "العلوم",
    "فيزياء":                "الفيزياء",
    "كيمياء":                "الكيمياء",
    "أحياء":                 "الأحياء",
    "احياء":                 "الأحياء",
    # Languages
    "لغة عربية":             "اللغة العربية",
    "لغتي":                  "لغتي",                  # already grammatical
    "لغتي الجميلة":          "لغتي الجميلة",
    "لغة إنجليزية":          "اللغة الإنجليزية",
    "لغة انجليزية":          "اللغة الإنجليزية",
    "إنجليزي":               "اللغة الإنجليزية",
    "انجليزي":               "اللغة الإنجليزية",
    # Social studies
    "دراسات اجتماعية":       "الدراسات الاجتماعية",
    "دراسات إجتماعية":       "الدراسات الاجتماعية",
    "اجتماعيات":             "الاجتماعيات",
    "تاريخ":                 "التاريخ",
    "جغرافيا":               "الجغرافيا",
    "جغرافية":               "الجغرافيا",
    # Religious subjects
    "تربية إسلامية":         "التربية الإسلامية",
    "تربية اسلامية":         "التربية الإسلامية",
    "دراسات إسلامية":        "الدراسات الإسلامية",
    "دراسات اسلامية":        "الدراسات الإسلامية",
    "فقه":                   "الفقه",
    "توحيد":                 "التوحيد",
    "حديث":                  "الحديث",
    "تفسير":                 "التفسير",
    "قرآن":                  "القرآن الكريم",
    "قرآن كريم":             "القرآن الكريم",
    "القرآن الكريم":         "القرآن الكريم",
    "تجويد":                 "التجويد",
    # Other common subjects
    "تربية بدنية":           "التربية البدنية",
    "تربية فنية":            "التربية الفنية",
    "حاسب آلي":              "الحاسب الآلي",
    "حاسب":                  "الحاسب الآلي",
    "حاسوب":                 "الحاسوب",
    "علوم الحاسب":           "علوم الحاسب",            # already grammatical
    "تقنية رقمية":           "التقنية الرقمية",
    "مهارات حياتية":         "المهارات الحياتية",
    "مهارات رقمية":          "المهارات الرقمية",
    "مهارات أسرية":          "المهارات الأسرية",
    "علم النفس":             "علم النفس",              # already grammatical
    "اقتصاد":                "الاقتصاد",
    "إدارة أعمال":           "إدارة الأعمال",
}


def _format_subject_with_al(subject: str | None) -> str:
    """Return the subject prefixed with the Arabic definite article ('ال')
    when needed, so the cover headline reads grammatically:

        "لمعلم الرياضيات"   (not "لمعلم رياضيات")
        "لمعلم الدراسات الاجتماعية"
        "لمعلم اللغة العربية"

    Strategy:
      1. Empty → empty string (caller should hide the line entirely).
      2. Exact match in `_SUBJECT_WITH_AL_OVERRIDES` → use the canonical form.
      3. Already starts with 'ال' / 'أل' → keep as-is.
      4. Default → prepend 'ال'.
    """
    if not subject:
        return ""
    s = subject.strip()
    if not s:
        return ""
    if s in _SUBJECT_WITH_AL_OVERRIDES:
        return _SUBJECT_WITH_AL_OVERRIDES[s]
    if s.startswith("ال") or s.startswith("أل"):
        return s
    return f"ال{s}"


def _file_data_uri(path: str | None, mime_type: str | None = None) -> str | None:
    """Return a browser-safe data URI for local media used inside Playwright PDF."""
    if not path:
        return None

    file_path = Path(path)
    if not file_path.exists() or not file_path.is_file():
        logger.warning("[PDF MEDIA MISSING] path=%s", path)
        return None

    guessed = mime_type or mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    try:
        encoded = base64.b64encode(file_path.read_bytes()).decode("ascii")
    except Exception as exc:
        logger.warning("[PDF MEDIA READ FAILED] path=%s error=%s", path, exc)
        return None

    return f"data:{guessed};base64,{encoded}"


def _image_data_uri(path: str | None) -> str | None:
    if not path:
        return None
    file_path = Path(path)
    if file_path.suffix.lower() not in _IMAGE_EXTS:
        return None
    return _file_data_uri(str(file_path))


# ── Evidence normalisation for PDF export ─────────────────────────────────────

# Titles that are raw/generic and must be replaced with a meaningful default.
_RAW_TITLE_PATTERNS: frozenset[str] = frozenset({
    "شاهد image", "شاهد video", "شاهد audio", "شاهد document",
    "شاهد pdf",   "شاهد url",   "شاهد text",  "شاهد voice",
    "image", "video", "audio", "document", "pdf", "url", "text", "voice",
    "", "شاهد", "null", "none", "undefined",
})

# Defaults per evidence_type: title, category, description used when stored values are weak.
_TYPE_EXPORT_DEFAULTS: dict[str, dict] = {
    "image":    {
        "title":    "نشاط تعليمي موثق بالصورة",
        "category": "نشاط صفي",
        "desc":     "صورة توثيقية تُبرز نشاطًا تعليميًا نفّذه المعلم داخل الفصل الدراسي.",
    },
    "video":    {
        "title":    "مقطع مرئي تعليمي موثق",
        "category": "نشاط صفي",
        "desc":     "مقطع مرئي يوثّق لحظة تعليمية أو تفاعلًا مع الطلاب داخل البيئة المدرسية.",
    },
    "audio":    {
        "title":    "تسجيل صوتي تعليمي",
        "category": "نشاط صفي",
        "desc":     "تسجيل صوتي يوثّق ملاحظة أو تعليقًا أو نشاطًا تعليميًا صوتيًا.",
    },
    "voice":    {
        "title":    "ملاحظة صوتية تعليمية",
        "category": "نشاط صفي",
        "desc":     "رسالة صوتية تحمل توجيهًا أو ملاحظة تربوية ذات قيمة توثيقية.",
    },
    "document": {
        "title":    "ملف تعليمي مرفق",
        "category": "ملف إداري",
        "desc":     "ملف رسمي أو وثيقة تعليمية يحتفظ بها المعلم ضمن ملف إنجازه المهني.",
    },
    "pdf":      {
        "title":    "وثيقة تعليمية PDF",
        "category": "ملف إداري",
        "desc":     "ملف PDF يحتوي على محتوى تعليمي أو وثيقة رسمية معتمدة.",
    },
    "url":      {
        "title":    "مصدر رقمي موثق",
        "category": "رابط إثرائي",
        "desc":     "رابط رقمي أو مصدر إلكتروني أرسله المعلم لتوثيق الإثراء والتقنية.",
    },
    "text":     {
        "title":    "ملاحظة تعليمية موثقة",
        "category": "نشاط صفي",
        "desc":     "نص أو رسالة دوّنها المعلم لتوثيق نشاط أو موقف أو توجيه تعليمي.",
    },
}
_DEFAULT_EXPORT = _TYPE_EXPORT_DEFAULTS["text"]


def _normalize_evidence_for_export(ev) -> dict:
    """
    Convert an Evidence ORM object to a clean export dict.

    Rules:
    • title    — replace generic/raw values with a meaningful type-based default.
    • category — must be in ALLOWED_CATEGORIES; if not, use type-based default.
    • description — use stored value if present; else use type-based educational default.

    The returned dict is what the PDF template receives — no raw fields reach the PDF.
    """
    ev_type  = (_clean_text(ev.evidence_type) or "text").lower()
    defaults = _TYPE_EXPORT_DEFAULTS.get(ev_type, _DEFAULT_EXPORT)

    # ── Title ─────────────────────────────────────────────────────────────────
    raw_title = _clean_text(ev.title) or ""
    has_custom_title = bool(raw_title and raw_title.lower() not in _RAW_TITLE_PATTERNS)
    title = (
        raw_title
        if has_custom_title
        else defaults["title"]
    )

    # ── Category / tags ───────────────────────────────────────────────────────
    raw_cat  = _clean_text(ev.category) or ""
    valid_categories = set(ALLOWED_CATEGORIES) | set(_MAIN_CATEGORY_ORDER) | set(_CATEGORY_META)
    sub_category = raw_cat if raw_cat in valid_categories else defaults["category"]
    category = _SUB_TO_MAIN_CATEGORY.get(sub_category, sub_category)
    category = _official_category_from_content(
        category,
        raw_title,
        _clean_text(ev.description),
        _clean_text(ev.message_text),
        raw_cat,
    )
    if category not in _MAIN_CATEGORY_ORDER and category not in _CATEGORY_META:
        category = _SUB_TO_MAIN_CATEGORY.get(defaults["category"], "التنفيذ داخل الصف")

    tags = []
    subject = _clean_text(ev.subject)
    grade = _clean_text(ev.grade)
    for value in (sub_category, subject, grade):
        if value and value.lower() not in _NULLISH_TEXT and value not in tags and value != category:
            tags.append(value)

    # ── Description ───────────────────────────────────────────────────────────
    stored_desc = _clean_text(ev.description) or ""
    # Also try to extract from message_text if no description and it's a text evidence
    description = stored_desc or defaults["desc"]
    enriched_description = _clean_text(getattr(ev, "ai_enriched_description", None))
    message_text = _clean_text(ev.message_text)
    media_url = _clean_text(ev.media_url)
    file_name = _clean_text(ev.file_name)
    mime_type = _clean_text(ev.mime_type)

    media_src: str | None = None
    media_available = False
    if ev_type == "image":
        media_src = _image_data_uri(ev.storage_path)
        media_available = bool(media_src)
    elif ev_type == "video":
        # Video storage_path should be a thumbnail from webhook for new records.
        # Older records may still point to .mp4; in that case render a video card fallback.
        media_src = _image_data_uri(ev.storage_path)
        media_available = bool(media_src or ev.storage_path)
    elif ev_type in ("audio", "voice"):
        media_available = bool(message_text or ev.storage_path)
    elif ev_type in ("pdf", "document") or file_name:
        media_available = True
    elif ev_type == "url":
        media_available = bool(message_text or media_url)

    raw_export_text = " ".join(
        value for value in (raw_title, raw_cat, stored_desc, message_text, file_name) if value
    )
    public_media_url = _public_media_url(ev_type, ev.storage_path, media_url)

    return {
        "id":            ev.id,
        "evidence_type": ev_type,
        "title":         title,
        "category":      category,
        "sub_category":  sub_category,
        "tags":          tags,
        "description":   description,
        "ai_enriched_description": enriched_description,
        "enriched_sections": _parse_enriched_sections(enriched_description),
        "message_text":  message_text,
        "media_url":     media_url,
        "storage_path":  ev.storage_path,
        "media_src":     media_src,
        "media_available": media_available,
        "file_name":     file_name,
        "file_label":    _friendly_file_label(file_name, ev_type, category),
        "file_type_label": _file_type_label(file_name, ev_type, mime_type),
        "mime_type":     mime_type,
        "link_type":     _link_type_label(category, title, message_text),
        "link_href":     _safe_link_href(media_url, message_text),
        "public_media_url": public_media_url,
        "download_url":  public_media_url,
        "subject":       subject,
        "grade":         grade,
        "created_at":    ev.created_at,
        "content_hash":  getattr(ev, "content_hash", None),  # used by export dedup
        "has_custom_title": has_custom_title,
        "has_custom_description": bool(stored_desc),
        "raw_export_text": raw_export_text,
        "was_normalised": stored_desc == "" or raw_title.lower() in _RAW_TITLE_PATTERNS,
        "is_excluded_from_export": bool(getattr(ev, "is_excluded_from_export", False)),
    }


def _should_export_evidence(ev: dict) -> bool:
    """Filter only export-noise records; never deletes anything from DB."""
    # Exclude system instructions regardless of category
    if is_instruction_evidence(ev):
        return False

    if ev["category"] != "أخرى":
        return True

    meaningful_parts = [
        ev.get("title"),
        ev.get("description") if ev.get("has_custom_description") else None,
        ev.get("message_text"),
        ev.get("file_name"),
        ev.get("media_url"),
    ]
    meaningful_text = " ".join(part for part in meaningful_parts if _has_meaningful_text(part))
    raw_text = ev.get("raw_export_text") or ""
    if _looks_malformed_text(raw_text):
        return False
    if ev["evidence_type"] == "text" and not _has_meaningful_text(meaningful_text, min_chars=8):
        return False
    return True


def _same_gallery_day(first: dict, other: dict) -> bool:
    a = first.get("created_at")
    b = other.get("created_at")
    if not a or not b:
        return False
    return a.date() == b.date()


def _can_join_image_gallery(first: dict, other: dict) -> bool:
    if other.get("evidence_type") != "image" or not other.get("media_src"):
        return False
    if first.get("category") != other.get("category"):
        return False
    if first.get("sub_category") != other.get("sub_category"):
        return False
    if not _same_gallery_day(first, other):
        return False

    # Respect deliberate, different captions: do not collapse distinct stories.
    first_desc = first.get("description") if first.get("has_custom_description") else None
    other_desc = other.get("description") if other.get("has_custom_description") else None
    if bool(first_desc) != bool(other_desc):
        return False
    if first_desc and other_desc and first_desc != other_desc:
        return False

    first_title = first.get("title") if first.get("has_custom_title") else None
    other_title = other.get("title") if other.get("has_custom_title") else None
    if bool(first_title) != bool(other_title):
        return False
    if first_title and other_title and first_title != other_title:
        return False

    if first.get("subject") and other.get("subject") and first["subject"] != other["subject"]:
        return False
    if first.get("grade") and other.get("grade") and first["grade"] != other["grade"]:
        return False

    return True


def _build_image_gallery(items: list[dict]) -> dict:
    first = items[0]
    description = next((item["description"] for item in items if item.get("has_custom_description")), first["description"])
    enriched_description = next((item.get("ai_enriched_description") for item in items if item.get("ai_enriched_description")), None)
    title = next((item["title"] for item in items if item.get("has_custom_title")), first["title"])
    tags = []
    for item in items:
        for tag in item.get("tags", []):
            if tag not in tags:
                tags.append(tag)

    return {
        **first,
        "id": f"gallery-{first['id']}",
        "evidence_type": "image_gallery",
        "title": title,
        "description": description,
        "ai_enriched_description": enriched_description,
        "enriched_sections": _parse_enriched_sections(enriched_description),
        "tags": tags[:6],
        "images": items,
        "gallery_count": len(items),
        "has_custom_title": any(item.get("has_custom_title") for item in items),
        "has_custom_description": any(item.get("has_custom_description") for item in items),
    }


def _group_image_galleries(evidences: list[dict]) -> list[dict]:
    grouped: list[dict] = []
    i = 0
    while i < len(evidences):
        current = evidences[i]
        if current.get("evidence_type") != "image" or not current.get("media_src"):
            grouped.append(current)
            i += 1
            continue

        run = [current]
        j = i + 1
        while j < len(evidences) and len(run) < 4 and _can_join_image_gallery(current, evidences[j]):
            run.append(evidences[j])
            j += 1

        if len(run) >= 2:
            grouped.append(_build_image_gallery(run))
            i += len(run)
        else:
            grouped.append(current)
            i += 1

    return grouped


def _evidence_export_count(ev: dict) -> int:
    if ev.get("evidence_type") == "image_gallery":
        return len(ev.get("images") or [])
    return 1


def _build_categories(normalised_evidences: list[dict]) -> list[dict]:
    """
    Group normalised evidence dicts by category.
    Every evidence passed here has already been cleaned by _normalize_evidence_for_export.
    Categories with zero evidences are excluded from the PDF.
    """
    grouped: dict[str, list] = {}
    for ev in normalised_evidences:
        cat = ev["category"]            # already validated — always in ALLOWED_CATEGORIES
        grouped.setdefault(cat, []).append(ev)

    # Canonical main-section order first, then any extra
    order  = list(_MAIN_CATEGORY_ORDER) + [c for c in grouped if c not in _MAIN_CATEGORY_ORDER]
    result = []
    for name in order:
        items = grouped.get(name, [])
        if not items:
            continue                    # skip empty categories
        items = _group_image_galleries(items)
        count = sum(_evidence_export_count(item) for item in items)
        if name == "أخرى" and count < 2:
            continue
        meta = _CATEGORY_META.get(name, _DEFAULT_META)
        result.append({
            "name":      name,
            "en":        meta["en"],
            "icon":      meta.get("icon", "📌"),
            "desc":      meta.get("desc", ""),
            "value":     meta.get("value", ""),
            "evidences": items,
            "count":     count,
        })
    return result


def _split_leading_categories(categories: list[dict]) -> tuple[list[dict], list[dict]]:
    leading_names = {"التخطيط", "سجل المتابعة"}
    leading = [cat for cat in categories if cat["name"] in leading_names]
    remaining = [cat for cat in categories if cat["name"] not in leading_names]
    return leading, remaining


def _build_performance_analysis(
    categories: list[dict],
    total_count: int,
    stats: dict | None = None,
    teacher=None,
    evidences: list[dict] | None = None,
) -> dict:
    nonempty = [cat for cat in categories if cat["count"] > 0]
    if not nonempty or total_count <= 0:
        return {}

    top = max(nonempty, key=lambda cat: cat["count"])
    low = min(nonempty, key=lambda cat: cat["count"])
    media_counts = [
        {"name": "الصور",      "count": (stats or {}).get("images", 0)},
        {"name": "الفيديو",    "count": (stats or {}).get("videos", 0)},
        {"name": "الصوتيات",   "count": (stats or {}).get("audios", 0)},
        {"name": "الملفات",    "count": (stats or {}).get("documents", 0)},
        {"name": "الروابط",    "count": (stats or {}).get("urls", 0)},
        {"name": "النصوص",     "count": (stats or {}).get("texts", 0)},
    ]
    top_media = max(media_counts, key=lambda item: item["count"])
    category_names = {cat["name"] for cat in nonempty}

    # ── Compute gaps (categories present in standard but missing from file) ───
    _EXPECTED_CATEGORIES = {"التخطيط", "التقويم", "مصادر تعليمية", "تواصل مع أولياء الأمور"}
    missing_cats = [c for c in _EXPECTED_CATEGORIES if c not in category_names]

    # ── Build portfolio summary for AI analysis ───────────────────────────────
    categories_summary = "، ".join(
        f"{cat['name']} ({cat['count']})" for cat in nonempty[:8]
    )
    media_summary = "، ".join(
        f"{m['name']} ({m['count']})" for m in media_counts if m["count"] > 0
    )
    # Sample top 5 evidences for context
    sample_lines: list[str] = []
    for ev in (evidences or [])[:5]:
        t = (ev.get("title") or "").strip()
        c = (ev.get("category") or "").strip()
        d = (ev.get("description") or "")[:80].strip()
        if t:
            sample_lines.append(f"- [{c}] {t}: {d}")
    sample_evidences = "\n".join(sample_lines) if sample_lines else ""

    # ── Try AI-powered portfolio analysis ─────────────────────────────────────
    ai_analysis: dict | None = None
    try:
        from app.services.gpt_brain import analyze_portfolio_sync
        ai_analysis = analyze_portfolio_sync(
            teacher_name=getattr(teacher, "name", None),
            subject=getattr(teacher, "subject", None),
            stage=getattr(teacher, "stage", None),
            grades=getattr(teacher, "grades", None),
            school_name=getattr(teacher, "school_name", None),
            total_count=total_count,
            categories_summary=categories_summary,
            media_summary=media_summary,
            top_category=top["name"],
            missing_categories="، ".join(missing_cats) if missing_cats else "لا يوجد",
            sample_evidences=sample_evidences,
        )
    except Exception as exc:
        logger.warning("[PORTFOLIO AI ANALYSIS FAILED] %s", exc)

    # ── Fallback static analysis if AI fails ─────────────────────────────────
    if ai_analysis:
        strengths      = ai_analysis.get("strengths", [])
        improvements   = ai_analysis.get("improvements", [])
        recommendations = ai_analysis.get("recommendations", [])
        note           = ai_analysis.get("overall_note", "")
    else:
        # Static fallback (previous logic preserved)
        note = (
            f"يعكس توزيع الشواهد تركيز المعلم على محور {top['name']}، "
            "مع حضور متوازن لبقية مجالات الأداء المهني. "
            "ويوصى بالاستمرار في تنويع الشواهد بين التخطيط والتنفيذ والتقويم والأنشطة الإثرائية."
        )
        strengths = [
            f"توفر شواهد نوعية في محور {top['name']} مما يعكس عناية واضحة بهذا الجانب من الأداء المهني.",
            "تنوع الوسائط المستخدمة يساعد على تقديم صورة أكثر واقعية عن ممارسات المعلم.",
        ]
        improvements = [
            f"تعزيز محور {c} بشواهد مباشرة يدعم اكتمال الملف المهني." for c in missing_cats[:2]
        ] or ["مواصلة توزيع الشواهد بين التخطيط والتنفيذ والتقويم للحفاظ على توازن الملف."]
        recommendations = [
            "اختيار الشواهد التي تُظهر أثر الممارسة على تعلم الطلاب وليس النشاط فقط.",
            "إضافة تأمل مهني قصير بعد كل نشاط بارز يوضح ما نجح وما يمكن تطويره.",
            "ربط الشواهد المستقبلية بمعايير الأداء مثل التخطيط والتنفيذ والتقويم.",
        ]

    return {
        "top_category":    {"name": top["name"], "count": top["count"]},
        "low_category":    {"name": low["name"], "count": low["count"]},
        "top_media":       top_media,
        "total_count":     total_count,
        "note":            note,
        "strengths":       strengths[:3],
        "improvements":    improvements[:3],
        "recommendations": recommendations[:3],
    }


def _build_stats(normalised_evidences: list[dict], categories: list[dict]) -> dict:
    """Build statistics dict for the summary/stats page."""
    counts: dict[str, int] = {
        "images": 0, "videos": 0, "audios": 0, "documents": 0, "urls": 0, "texts": 0
    }
    for ev in normalised_evidences:
        t = ev["evidence_type"]
        if t == "image":                    counts["images"]    += 1
        elif t in ("video",):               counts["videos"]    += 1
        elif t in ("audio", "voice"):       counts["audios"]    += 1
        elif t in ("pdf", "document"):      counts["documents"] += 1
        elif t == "url":                    counts["urls"]      += 1
        else:                               counts["texts"]     += 1

    nonempty  = sorted([c for c in categories if c["count"] > 0],
                       key=lambda c: c["count"], reverse=True)[:5]
    max_count = nonempty[0]["count"] if nonempty else 1
    top_categories = [
        {"name": c["name"], "count": c["count"], "pct": round(c["count"] / max_count * 100)}
        for c in nonempty
    ]
    return {
        **counts,
        "image_count": counts["images"],
        "video_count": counts["videos"],
        "audio_count": counts["audios"],
        "file_count": counts["documents"],
        "url_count": counts["urls"],
        "text_count": counts["texts"],
        "top_categories": top_categories,
    }


def _evidence_quality_score(ev) -> float:
    """Score evidence quality for smart/elite export modes."""
    score = 0.0
    if ev.storage_path:
        score += 5
    if ev.description:
        score += min(len(ev.description), 400) / 40
    if ev.title:
        score += min(len(ev.title), 100) / 25
    if ev.subject:
        score += 1
    if ev.grade:
        score += 1
    if ev.evidence_type in ("image", "video"):
        score += 2
    return score


def _select_evidences_for_mode(evidences: list, export_mode: str = "full") -> list:
    """
    Export modes:
      full  — all evidences
      smart — strongest 30 evidences, preserving quality and media
      elite — strongest 15 evidences only
    """
    mode = (export_mode or "full").lower()
    if mode == "full":
        return evidences

    limit = 30 if mode == "smart" else 15
    ranked = sorted(evidences, key=_evidence_quality_score, reverse=True)
    selected = ranked[:limit]
    logger.info(
        "[EXPORT MODE] mode=%s selected=%d/%d",
        mode, len(selected), len(evidences),
    )
    return selected


def _render_html(teacher: Teacher, evidences: list, *, include_intro_page: bool = False) -> str:
    """Render the portfolio HTML.

    The cover page is the final design embedded in `portfolio.html`
    (kept in sync with `app/templates/teacher_cover.html`). It is rendered
    as a fixed A4 page with `overflow: hidden`, so it can never spill into
    a phantom second page.

    `include_intro_page` controls the optional intro/credit/separator page.
    Default is False — empty intro pages are never rendered automatically.
    """
    from app.services.deduplication import deduplicate_for_export

    # ── Step 1: Normalise ALL evidences ──────────────────────────────────────
    # Guarantees every evidence has a proper title, category, and description.
    normalised = [_normalize_evidence_for_export(ev) for ev in evidences]
    n_fixed    = sum(1 for e in normalised if e["was_normalised"])
    if n_fixed:
        logger.info("[PDF NORMALISE] %d/%d evidences had missing metadata — defaults applied",
                    n_fixed, len(normalised))

    # ── Step 2: Filter noisy export-only records + teacher-excluded ones ─────
    # is_excluded_from_export is set by the review page — never deletes from DB.
    normalised = [ev for ev in normalised if _should_export_evidence(ev) and not ev.get("is_excluded_from_export")]

    # ── Step 3: Deduplicate before rendering ──────────────────────────────────
    # Safety net: removes duplicate evidences that slipped through save-time checks
    # (e.g. evidences added before dedup system was deployed, or content_hash=None).
    deduped   = deduplicate_for_export(normalised)
    n_removed = len(normalised) - len(deduped)
    if n_removed:
        logger.info("[PDF DEDUP] removed %d duplicate(s) before render", n_removed)

    categories = _build_categories(deduped)
    stats      = _build_stats(deduped, categories)
    leading_categories, remaining_categories = _split_leading_categories(categories)
    performance_analysis = _build_performance_analysis(
        categories, len(deduped), stats, teacher=teacher, evidences=deduped
    )

    logger.info(
        "[PDF RENDER] teacher_id=%s evidences=%d categories=%d include_intro_page=%s",
        getattr(teacher, "id", None), len(deduped), len(categories), include_intro_page,
    )

    template   = _jinja_env.get_template("portfolio.html")
    return template.render(
        teacher=teacher,
        categories=categories,
        leading_categories=leading_categories,
        remaining_categories=remaining_categories,
        performance_analysis=performance_analysis,
        stats=stats,
        total_count=len(deduped),
        academic_year=_academic_year(),
        generated_at=datetime.now().strftime("%Y/%m/%d %H:%M"),
        ministry_logo=_ministry_logo_svg_data_uri(),
        # Grammar-correct subject for the cover headline:
        # "لمعلم الرياضيات" instead of "لمعلم رياضيات".
        subject_with_al=_format_subject_with_al(getattr(teacher, "subject", None)),
        whatsapp_phone=_SUPPORT_WHATSAPP,
        whatsapp_url=f"https://wa.me/{_SUPPORT_WHATSAPP}",
        include_intro_page=include_intro_page,
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


async def run_export_background(teacher_id: int, export_id: int, export_mode: str = "full") -> None:
    """
    Background task: generate PDF → update record → send download link via WhatsApp.
    Opens its own DB session (avoids DetachedInstanceError).
    Sends WhatsApp message to teacher on success AND on failure — never silent.
    """
    from app.db.base import SessionLocal
    from app.services.teachers import get_teacher_by_id
    from app.services.whatsapp import send_whatsapp_button, send_whatsapp_message

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
        evidences = _select_evidences_for_mode(evidences, export_mode)

        logger.info(
            "[EXPORT STARTED] teacher_id=%d evidence_count=%d export_id=%d mode=%s",
            teacher_id, len(evidences), export_id, export_mode,
        )

        html = _render_html(teacher, evidences)

        export_dir = settings.export_storage(teacher_id)
        timestamp  = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename   = f"shawahid_teacher_{teacher_id}_{timestamp}.pdf"
        output_path = export_dir / filename

        await _generate_pdf(html, output_path)
        logger.info("[PDF GENERATED] path=%s", output_path)

        # Use the branded /d/ download UX (loading page + proper PDF headers)
        # instead of the raw /files/ static mount. This eliminates the blank-tab
        # flash on iOS Safari/Chrome and gives the user immediate visual
        # feedback that the system is working.
        # Backwards-compat: the raw /files/ URL still works for any legacy
        # WhatsApp message that already went out.
        pdf_url = (
            f"{settings.effective_base_url}/d"
            f"/{teacher_id}/{filename}"
        )
        record.storage_path = str(output_path)
        record.pdf_url      = pdf_url
        record.status       = "done"
        db.commit()

        logger.info("[PDF URL CREATED] teacher_id=%d url=%s", teacher_id, pdf_url)

        # ── Send download link to teacher via WhatsApp ────────────────────────
        success_msg = (
            f"✅ تم إنشاء ملف شواهدك بنجاح يا {teacher_name}.\n"
            "اضغط الزر لتحميل ملف الشواهد 📘"
        )
        sent = await send_whatsapp_button(
            teacher_phone,
            success_msg,
            "تحميل ملف الشواهد 📘",
            pdf_url,
            teacher_id=teacher_id,
        )
        if not sent:
            fallback_msg = (
                f"✅ تم إنشاء ملف شواهدك بنجاح يا {teacher_name}.\n"
                "اضغط هنا لتحميل ملف الشواهد:\n"
                f"{pdf_url}"
            )
            sent = await send_whatsapp_message(
                teacher_phone, fallback_msg, teacher_id=teacher_id, context="export_done_fallback"
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
