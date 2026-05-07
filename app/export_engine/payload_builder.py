"""
build_export_payload
────────────────────

Phase-1 façade: wraps the existing ``app.services.exporter`` helpers
without changing their behaviour. Inputs are ORM rows; output is a
pure :class:`ExportPayload`.

The renderer never sees the ORM. The template never sees the ORM.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.export_engine.media_resolver import resolve_media_for_evidence
from app.export_engine.pagination import (
    resolve_item_layout_mode,
    resolve_section_layout_mode,
)
from app.export_engine.schemas import (
    ExportCover,
    ExportItem,
    ExportPayload,
    ExportSchool,
    ExportSection,
    ExportSummary,
    ExportTeacher,
    IMPORTANCE_MEDIUM,
)


def build_export_payload(
    teacher: Any,
    evidences: list[Any],
    *,
    mode: str = "full",
) -> ExportPayload:
    """Produce a structured payload the renderer can consume directly.

    ``teacher`` and ``evidences`` are SQLAlchemy ORM rows in production,
    but the only thing this function does with them is read attributes
    (``getattr`` / public columns) — no commit, no flush, no refresh.

    The payload contains a ``legacy_context`` block so the existing
    Phase-1 template (still under ``app/templates/portfolio.html``)
    keeps rendering untouched. The structured ``sections`` field is
    what new themes built in Phase 2+ will read from.
    """
    # ── Lazy imports to avoid circulars and to keep the engine usable
    #    in tests where a slimmed-down ``exporter`` may not exist yet. ──
    from app.services.deduplication import deduplicate_for_export
    from app.services.exporter import (
        _academic_year,
        _build_categories,
        _build_performance_analysis,
        _build_stats,
        _format_subject_with_al,
        _ministry_logo_svg_data_uri,
        _normalize_evidence_for_export,
        _should_export_evidence,
        _split_leading_categories,
        _SUPPORT_WHATSAPP,
    )

    # ── Step 1: normalise → filter → dedup (same order as legacy). ───────
    normalised = [_normalize_evidence_for_export(ev) for ev in evidences]
    filtered = [
        ev
        for ev in normalised
        if _should_export_evidence(ev) and not ev.get("is_excluded_from_export")
    ]
    deduped = deduplicate_for_export(filtered)

    # ── Step 2: bucket into ordered categories with metadata. ────────────
    categories = _build_categories(deduped)
    stats = _build_stats(deduped, categories)
    leading_categories, remaining_categories = _split_leading_categories(categories)
    performance_analysis = _build_performance_analysis(
        categories, len(deduped), stats, teacher=teacher, evidences=deduped
    )

    # ── Step 3: project into structured DTOs. ────────────────────────────
    leading_names = {cat["name"] for cat in leading_categories}
    sections: list[ExportSection] = [
        _section_from_category(
            cat, order=idx, is_leading=cat["name"] in leading_names
        )
        for idx, cat in enumerate(categories)
    ]

    academic_year = _academic_year()
    generated_at = datetime.now().strftime("%Y/%m/%d %H:%M")

    cover = ExportCover(
        title="ملف الشواهد",
        subtitle=getattr(teacher, "subject", "") or "",
        academic_year=academic_year,
        generated_at=generated_at,
    )

    summary = ExportSummary(
        total_count=len(deduped),
        image_count=stats.get("image_count", 0),
        video_count=stats.get("video_count", 0),
        audio_count=stats.get("audio_count", 0),
        file_count=stats.get("file_count", 0),
        url_count=stats.get("url_count", 0),
        text_count=stats.get("text_count", 0),
        top_categories=list(stats.get("top_categories", [])),
    )

    payload = ExportPayload(
        teacher=ExportTeacher(
            id=getattr(teacher, "id", None),
            name=getattr(teacher, "name", "") or "",
            subject=getattr(teacher, "subject", "") or "",
            stage=getattr(teacher, "stage", "") or "",
            grades=getattr(teacher, "grades", "") or "",
        ),
        school=ExportSchool(
            name=getattr(teacher, "school_name", "") or "",
            principal_name=getattr(teacher, "principal_name", "") or "",
        ),
        export_mode=(mode or "full").lower(),
        generated_at=generated_at,
        cover=cover,
        summary=summary,
        sections=sections,
        legacy_context={
            # Everything the legacy portfolio.html template currently
            # consumes. Phase 2 will retire these one by one.
            "categories": categories,
            "leading_categories": leading_categories,
            "remaining_categories": remaining_categories,
            "performance_analysis": performance_analysis,
            "stats": stats,
            "academic_year": academic_year,
            "ministry_logo": _ministry_logo_svg_data_uri(),
            "subject_with_al": _format_subject_with_al(
                getattr(teacher, "subject", None)
            ),
            "whatsapp_phone": _SUPPORT_WHATSAPP,
            "whatsapp_url": f"https://wa.me/{_SUPPORT_WHATSAPP}",
        },
    )
    return payload


# ────────────────────────────────────────────────────────────────────────
# helpers
# ────────────────────────────────────────────────────────────────────────


def _section_from_category(
    cat: dict[str, Any],
    *,
    order: int,
    is_leading: bool = False,
) -> ExportSection:
    is_admin_grid = bool(cat.get("is_admin_grid"))
    items = [
        _item_from_evidence(ev, is_admin_section=is_admin_grid)
        for ev in cat.get("evidences", [])
    ]
    return ExportSection(
        key=cat.get("name", ""),
        title=cat.get("name", ""),
        order=order,
        description=cat.get("desc", ""),
        items=items,
        layout_mode=resolve_section_layout_mode(is_admin_grid=is_admin_grid),
        is_leading=is_leading,
        raw=cat,
    )


def _item_from_evidence(
    ev: dict[str, Any],
    *,
    is_admin_section: bool,
) -> ExportItem:
    importance = ev.get("importance", IMPORTANCE_MEDIUM)
    is_compact = bool(ev.get("is_compact"))

    enriched = ev.get("enriched_sections") or []
    objective = _section_text(enriched, ("الهدف التربوي", "الهدف"))
    student_impact = _section_text(enriched, ("الأثر على الطلاب", "أثر التعلم"))
    teacher_reflection = _section_text(enriched, ("تأمل المعلم", "التأمل"))
    ministry_standard = _section_text(
        enriched, ("الارتباط بالمعايير", "ارتباط المعايير")
    )

    return ExportItem(
        id=ev.get("id"),
        title=ev.get("title", "") or "",
        description=ev.get("ai_enriched_description") or ev.get("description") or "",
        evidence_type=ev.get("evidence_type", "") or "",
        media_type=ev.get("evidence_type", "") or "",
        category=ev.get("category", "") or "",
        subcategory=ev.get("sub_category", "") or "",
        ministry_standard=ministry_standard,
        objective=objective,
        student_impact=student_impact,
        teacher_reflection=teacher_reflection,
        importance_score=importance,
        layout_mode=resolve_item_layout_mode(
            is_admin_section=is_admin_section,
            importance=importance,
            is_compact=is_compact,
        ),
        media=resolve_media_for_evidence(ev),
        raw=ev,
    )


def _section_text(
    enriched: list[dict[str, Any]],
    label_aliases: tuple[str, ...],
) -> str:
    """Pick the first matching enriched-section text by label alias."""
    for section in enriched:
        label = (section.get("label") or "").strip()
        if any(alias in label for alias in label_aliases):
            return (section.get("text") or "").strip()
    return ""
