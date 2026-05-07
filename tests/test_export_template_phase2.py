"""
Phase-2 contract tests for ``app.templates.exports.ministry_v1``.

These tests assert the *template-level* boundary the Phase-2 plan
promises:

    1. ``template.html`` does not include ``portfolio.html`` anymore.
    2. ``theme.css`` and ``print.css`` exist, are non-empty, and are
       referenced from the template.
    3. The template module-set does not import ORM / DB code.
    4. ``render_template("ministry_v1", payload)`` returns valid HTML
       that contains the expected sections (cover, evidence cards,
       admin compact grid).
    5. ``_render_html`` (the legacy adapter) still works against the
       new template without crashing.

Tests intentionally avoid the database, OpenAI, Playwright, and any
real WhatsApp credentials — the autouse fixture in ``conftest.py``
short-circuits the GPT brain.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.export_engine import build_export_payload
from app.export_engine.renderer import render_template


# ──────────────────────────────────────────────────────────────────────
# Helpers (mirror tests/test_export_payload_contract.py to stay in sync)
# ──────────────────────────────────────────────────────────────────────


THEME_DIR = (
    Path(__file__).resolve().parent.parent
    / "app"
    / "templates"
    / "exports"
    / "ministry_v1"
)


def _fake_teacher() -> SimpleNamespace:
    return SimpleNamespace(
        id=999,
        phone="+966500000000",
        name="معلم اختبار",
        subject="رياضيات",
        stage="الابتدائية",
        grades="الرابع",
        school_name="مدرسة الاختبار",
        principal_name="أ. الاختبار",
    )


def _fake_evidence(**overrides) -> SimpleNamespace:
    base = dict(
        id=1,
        evidence_type="text",
        title="ملاحظة صفية",
        category="نشاط صفي",
        description="ملاحظة موثقة عن نشاط صفي.",
        message_text="نص الملاحظة كاملة كما كتبه المعلم في الرسالة.",
        media_url=None,
        storage_path=None,
        file_name=None,
        mime_type=None,
        subject="رياضيات",
        grade="الرابع",
        created_at=datetime(2026, 5, 1, 10, 0, 0),
        ai_enriched_description=None,
        content_hash=None,
        is_excluded_from_export=False,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ──────────────────────────────────────────────────────────────────────
# 1. Template no longer depends on portfolio.html
# ──────────────────────────────────────────────────────────────────────


def test_template_does_not_include_portfolio_html():
    """The Phase-2 template owns its own markup — no legacy bridge."""
    template = (THEME_DIR / "template.html").read_text(encoding="utf-8")
    # Look for the actual Jinja include directive, ignoring docstring
    # references to the historical Phase-1 bridge.
    import re

    include_directives = re.findall(
        r"{%\s*include\s+\"([^\"]+)\"\s*%}", template
    )
    assert "portfolio.html" not in include_directives, (
        "Phase 2 contract: ministry_v1/template.html must not include "
        "the legacy portfolio.html anymore."
    )
    assert "exports/ministry_v1/components/cover.html" in include_directives
    assert "exports/ministry_v1/components/section.html" in include_directives


# ──────────────────────────────────────────────────────────────────────
# 2. CSS files exist, are non-empty, and are linked from the template
# ──────────────────────────────────────────────────────────────────────


def test_theme_and_print_css_exist_and_are_linked():
    theme = THEME_DIR / "theme.css"
    print_ = THEME_DIR / "print.css"

    assert theme.exists(), "theme.css must exist for ministry_v1"
    assert print_.exists(), "print.css must exist for ministry_v1"

    theme_src = theme.read_text(encoding="utf-8")
    print_src = print_.read_text(encoding="utf-8")
    assert ":root" in theme_src, "theme.css must define the CSS variables"
    assert "@media print" in print_src or "page-break" in print_src, (
        "print.css must hold print/page-break rules"
    )

    template = (THEME_DIR / "template.html").read_text(encoding="utf-8")
    assert 'include "exports/ministry_v1/theme.css"' in template
    assert 'include "exports/ministry_v1/print.css"' in template


def test_print_css_contains_required_break_rules():
    """The plan mandates these page-break protections in CSS."""
    print_src = (THEME_DIR / "print.css").read_text(encoding="utf-8")
    for selector in (".evidence-card", ".media-card"):
        assert selector in print_src, (
            f"print.css must declare page-break rules for {selector}"
        )
    assert "break-inside: avoid" in print_src
    assert "page-break-inside: avoid" in print_src


# ──────────────────────────────────────────────────────────────────────
# 3. Template module-set does not import ORM / DB code
# ──────────────────────────────────────────────────────────────────────


def test_template_files_do_not_import_orm():
    """No `{% from %}` / `{% import %}` ever pulls Python ORM in.

    Jinja templates can't import Python modules anyway, but we still
    grep for accidental references that would imply a leaky bridge
    (e.g. someone hard-coding `Evidence.id` lookup syntax).
    """
    forbidden = (
        "sqlalchemy",
        "app.db.base",
        "app.models.evidence",
        "app.models.teacher",
        "session.query",
        ".filter(",
    )
    for path in THEME_DIR.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in text, (
                f"{path.name} accidentally references ORM symbol {needle!r}"
            )


# ──────────────────────────────────────────────────────────────────────
# 4. End-to-end render from ExportPayload
# ──────────────────────────────────────────────────────────────────────


def test_render_from_payload_smoke():
    payload = build_export_payload(
        _fake_teacher(),
        [_fake_evidence()],
        mode="full",
    )
    html = render_template("ministry_v1", payload)

    # Document shape
    assert "<!DOCTYPE html>" in html
    assert "<html lang=\"ar\" dir=\"rtl\">" in html
    assert "</body>" in html and "</html>" in html

    # Cover, philosophy, closing must all appear.
    assert "tcover-page" in html
    assert "ملف الشواهد" in html
    assert "خاتمة الملف" in html

    # The teacher name is rendered exactly (no escape mangling).
    assert "معلم اختبار" in html

    # Phase-2 canonical class lives alongside the legacy one.
    assert "evidence-card" in html

    # CSS must be inlined (Playwright rasterises with no network).
    assert ":root" in html
    assert "--teal:" in html


def test_render_admin_section_uses_compact_grid():
    """When a section's `layout_mode == 'compact_grid'`, the rendered
    HTML must wrap the cards in `.compact-grid` and mark them compact.
    """
    teacher = _fake_teacher()
    evidences = [
        _fake_evidence(
            id=10,
            evidence_type="pdf",
            title="تعميم رسمي",
            category="ملف إداري",
            description="تعميم من إدارة المدرسة.",
            file_name="circular.pdf",
            mime_type="application/pdf",
        ),
        _fake_evidence(
            id=11,
            evidence_type="pdf",
            title="جدول حصص",
            category="ملف إداري",
            description="جدول حصص أسبوعي.",
            file_name="timetable.pdf",
            mime_type="application/pdf",
        ),
    ]

    payload = build_export_payload(teacher, evidences, mode="full")
    html = render_template("ministry_v1", payload)

    assert 'class="compact-grid"' in html, (
        "Admin sections must render their items inside a .compact-grid "
        "wrapper when section.layout_mode == 'compact_grid'."
    )
    # Each card inside the admin grid must carry the `compact` class.
    assert "evidence-card  compact" in html or "evidence-card compact" in html or " compact\"" in html, (
        "Cards inside an admin compact_grid must carry the .compact class"
    )


def test_render_handles_empty_evidence_list():
    """Empty payload must still render cover + closing without crashing."""
    payload = build_export_payload(_fake_teacher(), [], mode="brief")
    html = render_template("ministry_v1", payload)

    assert "tcover-page" in html
    # No section pages should be rendered (the class appears in the
    # inlined CSS, hence we look for the HTML class attribute only).
    assert 'class="section-hero"' not in html
    # Stats / TOC / performance pages are skipped on empty.
    assert "ملخص الشواهد والإحصائيات" not in html
    assert "فهرس المحتويات" not in html


# ──────────────────────────────────────────────────────────────────────
# 5. Legacy `_render_html` adapter still works
# ──────────────────────────────────────────────────────────────────────


def test_legacy_render_html_adapter_still_works():
    """`exporter._render_html` must keep producing HTML against the
    new ministry_v1 template without raising.

    We bypass real DB Teacher/Evidence rows by feeding SimpleNamespace
    objects — `build_export_payload` is duck-typed (only reads attrs).
    """
    from app.services import exporter

    teacher = _fake_teacher()
    evidences = [_fake_evidence()]

    html = exporter._render_html(teacher, evidences, export_mode="full")
    assert isinstance(html, str) and len(html) > 1000
    assert "tcover-page" in html
    assert "ملف الشواهد" in html
