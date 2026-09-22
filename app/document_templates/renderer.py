"""Versioned JSON -> escaped Jinja HTML -> offline PDF.

Follows exam_engine's scoped loader and separate PDF renderer; the live exam
and evidence pipelines are deliberately unchanged. Student rendering never
receives solution data. This is a template pilot, not a source-file rewriter.
"""
from __future__ import annotations

import base64
import copy
import html as html_escape
import math
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates" / "documents" / "v1"
KINDS = {"worksheet", "exam", "weekly_plan"}
PROFILE_FIELDS = ("teacher", "school", "education_admin", "subject", "grade",
                  "semester", "date", "title", "week", "curriculum_edition", "academic_year")


def _text(value, field, maximum=2000):
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(f"{field}: expected text, max {maximum} characters")


def _math_tokens(tokens):
    if not isinstance(tokens, list) or len(tokens) > 30:
        raise ValueError("equation: expected at most 30 tokens")
    for token in tokens:
        if isinstance(token, str):
            _text(token, "math token", 40)
        elif isinstance(token, dict) and set(token) == {"numerator", "denominator"}:
            for value in token.values():
                _text(value, "fraction", 20)
        else:
            raise ValueError("equation: only text and simple fractions supported")


def _validate_table(table):
    if not isinstance(table, dict) or set(table) != {"headers", "rows"}:
        raise ValueError("invalid table")
    headers, rows = table["headers"], table["rows"]
    if not isinstance(headers, list) or not 1 <= len(headers) <= 6 or not isinstance(rows, list) or len(rows) > 8:
        raise ValueError("table exceeds supported dimensions")
    for row in [headers, *rows]:
        if not isinstance(row, list) or len(row) != len(headers):
            raise ValueError("table rows must match header count")
        for cell in row:
            _text(cell, "table cell", 180)


def validate_document(document: dict, answers: dict | None = None) -> None:
    if not isinstance(document, dict) or document.get("version") != 1:
        raise ValueError("document version must be 1")
    kind = document.get("kind")
    if kind not in KINDS:
        raise ValueError("unsupported document kind")
    profile = document.get("profile")
    if not isinstance(profile, dict) or set(profile) - set(PROFILE_FIELDS):
        raise ValueError("unknown or missing profile")
    for field in PROFILE_FIELDS:
        _text(profile.get(field, ""), field, 180)
    if not all(profile.get(key, "").strip() for key in ("subject", "grade", "title")):
        raise ValueError("subject, grade and title are required")
    _text(document.get("instructions", ""), "instructions", 500)
    _text(document.get("attribution", ""), "attribution", 500)
    if type(document.get("show_marks", False)) is not bool:
        raise ValueError("show_marks must be boolean")
    if "duration_minutes" in document and (type(document["duration_minutes"]) is not int or not 1 <= document["duration_minutes"] <= 300):
        raise ValueError("duration_minutes must be an integer from 1 to 300")
    if kind == "weekly_plan":
        days = document.get("days")
        if not isinstance(days, list) or not 1 <= len(days) <= 5:
            raise ValueError("weekly plan must contain 1 to 5 days")
        for day in days:
            if not isinstance(day, dict) or set(day) != {"day", "date", "lesson", "objective", "activity", "assessment", "homework"}:
                raise ValueError("invalid weekly plan columns")
            for value in day.values():
                _text(value, "plan cell", 400)
        if document.get("questions") or document.get("show_marks") or answers is not None:
            raise ValueError("weekly plans do not have questions, marks or answer keys")
        return
    questions = document.get("questions")
    if not isinstance(questions, list) or not 1 <= len(questions) <= 60:
        raise ValueError("expected 1 to 60 questions")
    ids = set()
    for q in questions:
        allowed = {"id", "text", "equation", "choices", "answer_lines", "marks", "table", "image"}
        if not isinstance(q, dict) or set(q) - allowed:
            raise ValueError("unknown question field (answers belong in a separate file)")
        _text(q.get("id"), "question id", 80)
        if q["id"] in ids or not q["id"]:
            raise ValueError("question IDs must be nonempty and unique")
        ids.add(q["id"])
        _text(q.get("text"), "question text", 1600)
        _math_tokens(q.get("equation", []))
        choices = q.get("choices", [])
        if not isinstance(choices, list) or len(choices) > 6:
            raise ValueError("expected at most 6 choices")
        for choice in choices:
            _text(choice, "choice", 200)
        lines = q.get("answer_lines", 1)
        if type(lines) is not int or not 0 <= lines <= 8:
            raise ValueError("answer_lines must be an integer from 0 to 8")
        marks = q.get("marks")
        if marks is not None and (type(marks) not in (int, float) or not math.isfinite(marks) or marks <= 0):
            raise ValueError("marks must be positive finite numbers")
        if (kind == "exam" or document.get("show_marks")) and marks is None:
            raise ValueError("every scored question needs marks")
        if "table" in q:
            _validate_table(q["table"])
        if "image" in q:
            image = q["image"]
            if not isinstance(image, dict) or set(image) != {"data", "alt", "attribution"}:
                raise ValueError("image needs data, alt and attribution")
            _text(image["alt"], "image alt", 300)
            _text(image["attribution"], "image attribution", 300)
            if not image["alt"].strip():
                raise ValueError("image alt is required")
            data = image["data"]
            if not isinstance(data, str) or not data.startswith("data:image/png;base64,") or len(data) > 2_000_000:
                raise ValueError("only inline PNGs up to 2 MB are supported; no remote URLs")
            try:
                raw = base64.b64decode(data.split(",", 1)[1], validate=True)
            except (ValueError, TypeError) as exc:
                raise ValueError("invalid image base64") from exc
            if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("invalid PNG signature")
    if kind == "exam" or document.get("show_marks"):
        total = document.get("total_marks")
        if type(total) not in (int, float) or not math.isfinite(total) or not math.isclose(total, sum(q["marks"] for q in questions), abs_tol=1e-9):
            raise ValueError("total_marks must equal the sum of question marks")
    if answers is not None:
        if not isinstance(answers, dict) or set(answers) != ids:
            raise ValueError("answer key must cover exactly the question IDs")
        for answer in answers.values():
            if not isinstance(answer, dict) or not {"text", "equation"} <= set(answer) or set(answer) - {"text", "equation", "table", "result"}:
                raise ValueError("each answer needs text and equation")
            _text(answer["text"], "answer", 1600)
            _math_tokens(answer["equation"])
            _text(answer.get("result", ""), "answer result", 300)
            if "table" in answer:
                _validate_table(answer["table"])


def render_document(document: dict, *, answers: dict | None = None) -> str:
    """Return portable editable HTML. Omit answers for a student-only document."""
    validate_document(document, answers)
    d = copy.deepcopy(document)
    d["profile"] = {field: d["profile"].get(field, "") for field in PROFILE_FIELDS}
    d.setdefault("instructions", "")
    d.setdefault("attribution", "")
    d.setdefault("duration_minutes", None)
    d["show_marks"] = d["kind"] == "exam" or d.get("show_marks", False)
    for q in d.get("questions", []):
        for key, value in {"equation": [], "choices": [], "answer_lines": 1, "marks": None, "table": None, "image": None}.items():
            q.setdefault(key, value)
    env = Environment(loader=FileSystemLoader(TEMPLATES), undefined=StrictUndefined,
                      autoescape=select_autoescape(("html", "xml")), trim_blocks=True,
                      lstrip_blocks=True)
    env.filters["ar"] = lambda value: str(value).translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"))
    font = base64.b64encode((ROOT / "static/documents/NotoSansArabic.ttf").read_bytes()).decode("ascii")
    return env.get_template("template.html").render(d=d, answers=answers, font=font)


def export_pdf(document: dict, *, answers: dict | None = None) -> bytes:
    """Print the new template only. Fail visibly on overflow or missing images.

    No HTML fallback is silently labelled PDF. Offline context blocks every
    external request, even if future template changes accidentally add one.
    """
    from playwright.sync_api import sync_playwright

    html = render_document(document, answers=answers)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context = browser.new_context(offline=True, java_script_enabled=False)
            context.route("**/*", lambda route: route.abort())
            page = context.new_page()
            page.set_content(html, wait_until="load")
            page.emulate_media(media="print")
            page.evaluate("document.fonts.ready")
            failures = page.evaluate("""() => {
              const faults=[];
              const maxHeight=(document.body.classList.contains('weekly_plan') ? 182 : 269)*96/25.4;
              for (const img of document.images) if (!img.complete || !img.naturalWidth) faults.push('broken image');
              for (const block of document.querySelectorAll('.keep,.question-row')) {
                if (block.getBoundingClientRect().height > maxHeight) faults.push('oversized block');
                if (block.scrollWidth > block.clientWidth + 2) faults.push('horizontal overflow');
              }
              return faults;
            }""")
            if failures:
                raise ValueError("Manual layout review required: " + ", ".join(failures))
            pdf_bytes = page.pdf(prefer_css_page_size=True, print_background=True)
        finally:
            browser.close()
    # Chromium's built-in page numbers are Latin only. Stamp Arabic-Indic
    # numbers in the reserved bottom margin without touching page content.
    import fitz
    with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf:
        font_path = ROOT / "static/documents/NotoSansArabic.ttf"
        font = fitz.Font(fontfile=str(font_path))
        for n, sheet in enumerate(pdf, 1):
            if document['kind'] == 'exam' and n > 1:
                # A detached exam sheet must still identify its subject/student.
                # Reserve the existing top margin; do not overlay question text.
                profile = document['profile']
                title = html_escape.escape(profile['title'])
                subject = html_escape.escape(profile['subject'])
                grade = html_escape.escape(profile['grade'])
                continuation = (f'<div dir="rtl"><b>{title}</b><br>'
                                f'{subject} · {grade} · اسم الطالب: ................................</div>')
                spare, scale = sheet.insert_htmlbox(
                    fitz.Rect(14*72/25.4, 2*72/25.4, sheet.rect.width-14*72/25.4, 13*72/25.4),
                    continuation,
                    css="@font-face{font-family:Shawahid;src:url(NotoSansArabic.ttf)}"
                        "*{margin:0;padding:0}div{font-family:Shawahid;font-size:8pt;line-height:1.15;text-align:right;color:#315a5b}",
                    archive=fitz.Archive(str(font_path.parent)), scale_low=0.8)
                if spare < 0:
                    raise ValueError('Manual layout review required: continuation header too long')
            label = f"{n} / {len(pdf)}".translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"))
            sheet.insert_font(fontname="ShawahidPage", fontfile=str(font_path))
            sheet.insert_text(((sheet.rect.width-font.text_length(label, fontsize=8))/2,
                               sheet.rect.height-17), label, fontname="ShawahidPage",
                              fontsize=8, color=(0.3,0.36,0.38))
        pdf.subset_fonts()
        return pdf.tobytes(garbage=4, deflate=True)
