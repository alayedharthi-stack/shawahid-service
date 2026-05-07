"""
Phase-1 contract test for ``app.export_engine``.

Covers exactly the boundary the migration plan promises:

    1. ``build_export_payload`` returns a typed ``ExportPayload``.
    2. The builder does not depend on ``theme.css`` / ``print.css`` —
       editing those files cannot change a single byte of the payload.
    3. The renderer stays decoupled from the ORM (it accepts a typed
       payload only).

Tests intentionally avoid the database, OpenAI, and Playwright. They
build evidence rows from ``SimpleNamespace`` so the legacy normalisation
path inside ``app.services.exporter`` runs end-to-end without touching
external services.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.export_engine import (
    ExportPayload,
    build_export_payload,
)
from app.export_engine.schemas import (
    LAYOUT_COMPACT,
    SECTION_LAYOUT_COMPACT_GRID,
)


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


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


def _theme_css_path() -> Path:
    return (
        Path(__file__).resolve().parent.parent
        / "app"
        / "templates"
        / "exports"
        / "ministry_v1"
        / "theme.css"
    )


# ──────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────


def test_payload_basic_shape():
    """An empty evidence list still produces a valid payload."""
    payload = build_export_payload(_fake_teacher(), [], mode="full")

    assert isinstance(payload, ExportPayload)
    assert payload.export_mode == "full"
    assert payload.teacher.id == 999
    assert payload.teacher.name == "معلم اختبار"
    assert payload.school.name == "مدرسة الاختبار"
    assert payload.summary.total_count == 0
    assert payload.sections == []


def test_payload_export_mode_normalised():
    """Mode is lowercased so downstream comparisons are stable."""
    payload = build_export_payload(_fake_teacher(), [], mode="SMART")
    assert payload.export_mode == "smart"


def test_payload_admin_section_marked_compact_grid():
    """Items in an administrative section render as a compact grid."""
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
        )
    ]

    payload = build_export_payload(teacher, evidences, mode="full")

    admin_sections = [s for s in payload.sections if s.title == "ملفات إدارية"]
    assert len(admin_sections) == 1
    section = admin_sections[0]
    assert section.layout_mode == SECTION_LAYOUT_COMPACT_GRID
    assert all(item.layout_mode == LAYOUT_COMPACT for item in section.items)


def test_payload_independent_of_theme_css(tmp_path: Path) -> None:
    """Mutating theme.css must not change the produced payload bytes.

    This is the hard contract from the migration plan: the export
    engine never reads CSS.
    """
    teacher = _fake_teacher()
    evidences = [_fake_evidence()]

    payload_before = build_export_payload(teacher, evidences, mode="full")

    theme = _theme_css_path()
    assert theme.exists(), "ministry_v1/theme.css must exist as the canonical theme entry"
    original = theme.read_text(encoding="utf-8")
    try:
        # Pretend a designer rewrote the entire stylesheet — payload
        # must be unaffected.
        theme.write_text(
            "/* mutated for contract test */\nbody { background: hotpink; }\n",
            encoding="utf-8",
        )
        payload_after = build_export_payload(teacher, evidences, mode="full")
    finally:
        theme.write_text(original, encoding="utf-8")

    # Compare on the structured fields. ``generated_at`` is a clock
    # timestamp so we exclude it from the equality check.
    a = _payload_dict_for_compare(payload_before)
    b = _payload_dict_for_compare(payload_after)
    assert a == b, "theme.css affected the payload — engine boundary leaked"


def test_renderer_does_not_import_orm():
    """The renderer module must not pull in SQLAlchemy or DB code."""
    import app.export_engine.renderer as renderer

    forbidden = (
        "sqlalchemy",
        "app.db.base",
        "app.models.evidence",
        "app.models.teacher",
    )
    for name in forbidden:
        assert not _module_imports(renderer, name), (
            f"renderer.py must not import {name}"
        )


# ──────────────────────────────────────────────────────────────────────


def _payload_dict_for_compare(payload: ExportPayload) -> dict:
    data = asdict(payload)
    # Strip clock-dependent fields and the legacy bridge (which holds
    # callable-free mutable structures and is not part of the typed
    # contract guaranteed across phases).
    data.pop("generated_at", None)
    if "cover" in data:
        data["cover"].pop("generated_at", None)
    data.pop("legacy_context", None)
    return data


def _module_imports(module, target: str) -> bool:
    """Return True if any import line in ``module`` mentions ``target``."""
    source = Path(module.__file__).read_text(encoding="utf-8")
    return any(
        target in line
        for line in source.splitlines()
        if line.strip().startswith(("import ", "from "))
    )
