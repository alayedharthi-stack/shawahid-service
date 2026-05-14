"""
exam_rewrite.flow — orchestrator that ties extractor + cleaner +
metadata + parser together and produces a :class:`StructuredExam`.

Phase-3 hard rules:
    • No GPT, no DB, no network.
    • No PDF generation, no HTML.
    • No saving anything as evidence.
    • Pure: same inputs → same StructuredExam.

Two entry points exist on purpose:

``analyze_exam_pdf(storage_path, ...)``
    Read the PDF from disk and run the full pipeline. Returns a
    StructuredExam, never ``None`` — the ``warnings`` field describes
    any gap so the webhook can decide what to tell the teacher.

``analyze_exam_text(raw_text, ...)``
    Skip the PDF I/O step. Used by tests and by the webhook when it
    already has the extracted text in hand from Phase 1.
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.exam_rewrite.extractor import extract_all_pages, join_pages
from app.exam_rewrite.metadata_extractor import (
    detect_exam_type,
    detect_grade,
    detect_instructions,
    detect_subject,
    detect_title,
    detect_total_score,
)
from app.exam_rewrite.question_parser import parse_sections
from app.exam_rewrite.schemas import (
    EXAM_TYPE_EXAM,
    EXAM_TYPE_UNKNOWN,
    StructuredExam,
)
from app.exam_rewrite.text_cleaner import clean_lines, clean_text

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────


def analyze_exam_pdf(
    storage_path: str | Path | None,
    *,
    detected_type_hint: str | None = None,
    teacher_subject: str | None = None,
    teacher_grades: str | None = None,
    fallback_text: str | None = None,
) -> StructuredExam:
    """Read the PDF at ``storage_path`` and produce a StructuredExam.

    Falls back gracefully:
        • Missing path / unreadable PDF → uses ``fallback_text`` if
          supplied, otherwise returns a StructuredExam with warnings
          and zero sections.
        • Empty / scanned PDF → same fallback behaviour.

    The teacher-profile fields fill blanks the PDF didn't mention so
    the rewritten exam can still carry the right subject / grade.
    """
    raw_text = ""
    if storage_path:
        pages = extract_all_pages(storage_path)
        if pages:
            raw_text = join_pages(pages)

    if not raw_text and fallback_text:
        raw_text = fallback_text

    return analyze_exam_text(
        raw_text,
        detected_type_hint=detected_type_hint,
        teacher_subject=teacher_subject,
        teacher_grades=teacher_grades,
    )


def analyze_exam_text(
    raw_text: str | None,
    *,
    detected_type_hint: str | None = None,
    teacher_subject: str | None = None,
    teacher_grades: str | None = None,
) -> StructuredExam:
    """Run the analysis pipeline on already-extracted text.

    The function never raises — on bad / empty input it returns a
    StructuredExam whose ``warnings`` field explains what went wrong.
    """
    warnings: list[str] = []

    if not raw_text or not raw_text.strip():
        warnings.append("لم يتم استخراج نص من الملف.")
        return StructuredExam(
            subject=teacher_subject or None,
            grade=teacher_grades or None,
            exam_type=detected_type_hint or EXAM_TYPE_UNKNOWN,
            warnings=tuple(warnings),
        )

    # ── Clean ────────────────────────────────────────────────────────
    cleaned = clean_text(raw_text)
    lines = clean_lines(raw_text)

    if not cleaned.strip():
        warnings.append("النص بعد التنظيف فارغ — قد يكون الملف ممسوحًا ضوئيًا.")
        return StructuredExam(
            subject=teacher_subject or None,
            grade=teacher_grades or None,
            exam_type=detected_type_hint or EXAM_TYPE_UNKNOWN,
            warnings=tuple(warnings),
        )

    # ── Metadata ─────────────────────────────────────────────────────
    subject = detect_subject(cleaned) or teacher_subject or None
    if not subject:
        warnings.append("تعذّر اكتشاف المادة.")

    grade = detect_grade(cleaned) or teacher_grades or None
    if not grade:
        warnings.append("تعذّر اكتشاف الصف.")

    exam_type = detect_exam_type(cleaned, hint=detected_type_hint)
    if exam_type == EXAM_TYPE_UNKNOWN:
        # Default — we already know the teacher chose 'rewrite'.
        exam_type = EXAM_TYPE_EXAM

    title = detect_title(lines)
    if not title:
        warnings.append("تعذّر اكتشاف عنوان الاختبار.")

    instructions = detect_instructions(lines)
    total_score = detect_total_score(cleaned)

    # ── Questions ────────────────────────────────────────────────────
    sections = parse_sections(lines)
    if not sections:
        warnings.append("لم يتم العثور على أسئلة واضحة في الملف.")

    if not sections and not subject and not grade:
        warnings.append("الملف غير كافٍ للتحليل.")

    structured = StructuredExam(
        subject=subject,
        grade=grade,
        exam_type=exam_type,
        title=title,
        instructions=instructions,
        total_score=total_score,
        sections=tuple(sections),
        warnings=tuple(warnings),
    )

    logger.info(
        "[EXAM ANALYSIS] subject=%r grade=%r type=%s sections=%d questions=%d "
        "title=%r warnings=%d",
        structured.subject,
        structured.grade,
        structured.exam_type,
        len(structured.sections),
        structured.total_questions,
        (structured.title or "")[:60],
        len(structured.warnings),
    )
    return structured


__all__ = ["analyze_exam_pdf", "analyze_exam_text"]
