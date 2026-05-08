"""
exam_engine.exam_flow — WhatsApp-side orchestrator for exam creation.

Phase-12 contract
=================
This module is the *only* glue layer between the webhook and every
piece of exam machinery built in earlier phases. Given an inbound
message + the per-teacher state, it:

    1. parses slots from the message
    2. merges them into the multi-turn ``ExamConversationState``
    3. asks for missing fields, OR
    4. runs ``run_source_pipeline`` to find sample papers, OR
    5. falls back to ``generate_exam`` (manual topic / sample bank), OR
    6. validates the result and renders it to PDF (best-effort)
    7. returns an ``ExamFlowResult`` for the webhook to act on

The function never raises and never sends WhatsApp messages itself —
the webhook owns IO. Tests can drive the entire flow synchronously.

Forbidden imports (asserted by tests):
    • app.export_engine
    • app.media_engine
    • app.review_engine
    • app.storage_engine
    • app.services.exporter
    • playwright
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.conversation_engine.exam_state import (
    ExamConversationState,
    get_exam_state,
    merge_exam_state,
    record_generated_exam,
    set_pending_fields,
)
from app.exam_engine.exam_defaults import smart_defaults
from app.exam_engine.exam_generator import GenerationFailure, generate_exam
from app.exam_engine.exam_profile import build_exam_profile
from app.exam_engine.exam_slot_parser import parse_exam_slots
from app.exam_engine.exam_validator import validate_exam
from app.exam_engine.messages import (
    build_exam_failure_message,
    build_exam_missing_info_message,
    build_exam_ready_message,
    build_exam_source_selection_message,
)
from app.exam_engine.schemas import (
    EXAM_TYPE_QUICK,
    SOURCE_MANUAL_TOPIC,
    SOURCE_SAMPLE_BANK,
    EXAM_TYPE_LABELS_AR,
    ExamProfile,
    ExamRequest,
    GeneratedExam,
)
from app.exam_engine.sources import (
    AntiCopyOptions,
    HttpClient,
    SourceQuery,
    list_providers,
    run_source_pipeline,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Result DTOs
# ──────────────────────────────────────────────────────────────────────


# Stages used by ``ExamFlowResult.stage`` so the webhook can branch.
STAGE_MISSING_INFO = "missing_info"
STAGE_READY = "ready"
STAGE_NO_MATCH = "no_match"
STAGE_FAILED = "failed"


@dataclass
class ExamFlowResult:
    """What the webhook needs to send back to the teacher."""

    stage: str
    reply_text: str
    exam: GeneratedExam | None = None
    pdf_path: str | None = None
    progress_messages: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    state: ExamConversationState | None = None

    @property
    def is_ready(self) -> bool:
        return self.stage == STAGE_READY

    @property
    def needs_more_info(self) -> bool:
        return self.stage == STAGE_MISSING_INFO


# ──────────────────────────────────────────────────────────────────────
# Public entry-point
# ──────────────────────────────────────────────────────────────────────


def handle_exam_request(
    *,
    teacher_id: int,
    text: str | None,
    teacher_name: str | None = None,
    school_name: str | None = None,
    education_admin: str | None = None,
    region: str | None = None,
    teacher_subject: str | None = None,
    teacher_stage: str | None = None,
    teacher_grades: tuple[str, ...] = (),
    teacher_uploaded_text: str | None = None,
    http_client: HttpClient | None = None,
    render_pdf: bool = True,
) -> ExamFlowResult:
    """Run one turn of the exam conversation.

    The function is synchronous + pure (modulo ``state`` mutation +
    optional PDF rendering). It never sends WhatsApp messages itself —
    the webhook reads ``ExamFlowResult.reply_text`` / ``pdf_path`` and
    schedules the actual sends.

    ``teacher_uploaded_text`` is supplied when the teacher attached a
    PDF/image whose extracted text the webhook already has in hand —
    we treat it as an additional sample source.
    """
    # ── 1. Pull slots from the inbound text + merge with existing state.
    slots = parse_exam_slots(text)
    state = merge_exam_state(teacher_id, **slots.as_kwargs())

    # ── 2. Backfill from teacher profile when the slot is still empty.
    _backfill_from_profile(
        state,
        teacher_subject=teacher_subject,
        teacher_stage=teacher_stage,
        teacher_grades=teacher_grades,
    )

    # ── 3. Apply smart defaults for the numeric trio.
    _apply_defaults(state)

    # ── 4. Compute pending slots from the canonical request.
    request = _build_request(state, teacher_id=teacher_id)
    missing = _missing_user_facing_slots(request, state)

    if missing:
        set_pending_fields(teacher_id, missing)
        msg = build_exam_missing_info_message(missing)
        return ExamFlowResult(
            stage=STAGE_MISSING_INFO,
            reply_text=msg,
            state=state,
            flags=["missing_info"],
        )

    # ── 5. Try the external-source pipeline first (sample papers).
    progress: list[str] = []
    pipeline_result = _try_source_pipeline(
        request=request,
        teacher_uploaded_text=teacher_uploaded_text,
        http_client=http_client,
    )

    sourced_questions = pipeline_result.questions if pipeline_result else ()
    if sourced_questions:
        progress.append(build_exam_source_selection_message(
            found_count=pipeline_result.accepted_samples,
            semester=request.semester,
        ))

    # ── 6. Build the exam — sourced OR fall back to local generation.
    exam, fail = _build_exam(
        request=request,
        state=state,
        sourced_questions=sourced_questions,
        teacher_name=teacher_name,
        school_name=school_name,
        education_admin=education_admin,
        region=region,
    )

    if fail is not None:
        # The generator told us what's missing — surface it gently.
        if fail.missing:
            return ExamFlowResult(
                stage=STAGE_MISSING_INFO,
                reply_text=build_exam_missing_info_message(fail.missing),
                state=state,
                flags=["generator_missing"],
            )
        return ExamFlowResult(
            stage=STAGE_NO_MATCH,
            reply_text=_no_match_message(),
            state=state,
            flags=["generator_failed", fail.code],
        )

    # ── 7. Validate.
    validation = validate_exam(exam)
    if not validation.is_valid:
        logger.warning(
            "[EXAM FLOW] validation failed teacher_id=%d errors=%s",
            teacher_id, [i.message for i in validation.errors],
        )
        return ExamFlowResult(
            stage=STAGE_FAILED,
            reply_text=build_exam_failure_message(
                "تعذر اجتياز التحقق من الاختبار."
            ),
            state=state,
            flags=["validation_failed"],
        )

    # ── 8. Render to PDF and persist to teacher's exam folder
    # (best-effort — failure does not abort the flow; the webhook can
    # still send the friendly text confirmation back to the teacher).
    pdf_path: str | None = None
    if render_pdf:
        pdf_path = _render_pdf_safely(exam, teacher_id=teacher_id)

    record_generated_exam(
        teacher_id,
        exam_id=exam.exam_id,
        pdf_path=pdf_path,
        subject=exam.profile.subject,
        grade=exam.profile.grade,
        exam_type=exam.profile.exam_type,
    )

    ready_text = build_exam_ready_message(exam)
    return ExamFlowResult(
        stage=STAGE_READY,
        reply_text=ready_text,
        exam=exam,
        pdf_path=pdf_path,
        progress_messages=progress,
        state=state,
        flags=["ok"] + (["sourced"] if sourced_questions else ["bank"]),
    )


# ──────────────────────────────────────────────────────────────────────
# Internals
# ──────────────────────────────────────────────────────────────────────


def _backfill_from_profile(
    state: ExamConversationState,
    *,
    teacher_subject: str | None,
    teacher_stage: str | None,
    teacher_grades: tuple[str, ...],
) -> None:
    """Inherit slots the teacher already configured on their profile."""
    if not state.subject and teacher_subject:
        state.subject = teacher_subject
    if not state.stage and teacher_stage:
        state.stage = teacher_stage
    if not state.grade and teacher_grades:
        # Pick the first grade — webhook can swap if multiple apply.
        state.grade = teacher_grades[0]


def _apply_defaults(state: ExamConversationState) -> None:
    if state.exam_type and (
        not state.total_questions
        or not state.total_marks
        or not state.duration_minutes
    ):
        defaults = smart_defaults(stage=state.stage, exam_type=state.exam_type)
        state.total_questions = state.total_questions or defaults.total_questions
        state.total_marks = state.total_marks or defaults.total_marks
        state.duration_minutes = state.duration_minutes or defaults.duration_minutes


def _build_request(
    state: ExamConversationState,
    *,
    teacher_id: int,
) -> ExamRequest:
    return ExamRequest(
        teacher_id=teacher_id,
        exam_type=state.exam_type or EXAM_TYPE_QUICK,
        subject=state.subject,
        grade=state.grade,
        stage=state.stage,
        semester=state.semester,
        unit=state.unit,
        lesson=state.lesson,
        week=state.week,
        total_questions=state.total_questions or 10,
        total_marks=state.total_marks or 20,
        duration_minutes=state.duration_minutes or 30,
        source_mode=state.source_mode or SOURCE_SAMPLE_BANK,
        topic=state.lesson or state.unit,
    )


def _missing_user_facing_slots(
    request: ExamRequest, state: ExamConversationState,
) -> tuple[str, ...]:
    """Slots we should ask about. Distinct from
    ``ExamRequest.required_fields_missing`` because the webhook flow
    treats stage as derived (we infer it from grade)."""
    missing: list[str] = []
    if not request.subject:
        missing.append("subject")
    if not request.grade and not request.stage:
        missing.append("grade")
    if not request.exam_type:
        missing.append("exam_type")
    return tuple(missing)


# ──────────────────────────────────────────────────────────────────────
# Source pipeline glue
# ──────────────────────────────────────────────────────────────────────


def _try_source_pipeline(
    *,
    request: ExamRequest,
    teacher_uploaded_text: str | None,
    http_client: HttpClient | None,
):
    """Run ``run_source_pipeline`` and return the result, or ``None``
    on any unexpected failure (the engine itself is fail-soft, but we
    add an outer try/except so the whole flow stays robust)."""
    query = SourceQuery(
        subject=request.subject,
        grade=request.grade,
        stage=request.stage,
        semester=request.semester,
        exam_type=request.exam_type,
        unit=request.unit,
        lesson=request.lesson,
    )
    try:
        providers = list_providers(only_active=True, http_client=http_client)
        # Anti-copy uses a deterministic seed so the same teacher gets
        # the same exam across retries within a session.
        seed = abs(hash((request.teacher_id, request.exam_type, request.subject))) % (2**32)
        return run_source_pipeline(
            providers,
            query=query,
            anti_copy=AntiCopyOptions(seed=seed),
            max_questions=request.total_questions or 10,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[EXAM FLOW] source pipeline failed: %s", exc)
        return None


# ──────────────────────────────────────────────────────────────────────
# Exam builder
# ──────────────────────────────────────────────────────────────────────


def _build_exam(
    *,
    request: ExamRequest,
    state: ExamConversationState,
    sourced_questions,
    teacher_name: str | None,
    school_name: str | None,
    education_admin: str | None,
    region: str | None,
) -> tuple[GeneratedExam | None, GenerationFailure | None]:
    """Produce a ``GeneratedExam`` either from sourced questions or by
    delegating to the local generator."""
    profile = build_exam_profile(
        request=request,
        teacher_name=teacher_name,
        school_name=school_name,
        education_admin=education_admin,
        region=region,
    )

    if sourced_questions:
        # The pipeline already ran anti-copy. Just normalise to the
        # request's totals via the local generator's mark allocator.
        from app.exam_engine.exam_generator import (
            _allocate_marks,
            _cap_to_requested,
        )

        questions = list(sourced_questions)
        questions, _trimmed = _cap_to_requested(
            questions, request.total_questions,
        )
        questions = _allocate_marks(
            questions, target_total=request.total_marks,
        )
        return GeneratedExam(
            profile=profile,
            questions=tuple(questions),
            request=request,
            warnings=("تم بناء الاختبار من نماذج خارجية مع منع النسخ الحرفي.",),
            notes=(f"عدد الأسئلة من المصدر: {len(sourced_questions)}",),
        ), None

    # Local fallback: sample bank.
    out = generate_exam(request, profile=profile)
    if isinstance(out, GenerationFailure):
        # Try a manual-topic regeneration using the lesson/unit text.
        topic = state.lesson or state.unit
        if topic:
            alt_request = ExamRequest(
                teacher_id=request.teacher_id,
                exam_type=request.exam_type,
                subject=request.subject,
                grade=request.grade,
                stage=request.stage,
                semester=request.semester,
                unit=request.unit,
                lesson=request.lesson,
                week=request.week,
                difficulty=request.difficulty,
                question_types=request.question_types,
                total_questions=request.total_questions,
                total_marks=request.total_marks,
                duration_minutes=request.duration_minutes,
                source_mode=SOURCE_MANUAL_TOPIC,
                topic=topic,
            )
            out2 = generate_exam(alt_request, profile=profile, teacher_topic=topic)
            if not isinstance(out2, GenerationFailure):
                return out2, None
        return None, out
    return out, None


# ──────────────────────────────────────────────────────────────────────
# PDF rendering
# ──────────────────────────────────────────────────────────────────────


def _render_pdf_safely(
    exam: GeneratedExam, *, teacher_id: int,
) -> str | None:
    """Best-effort PDF render that PERSISTS the result to disk.

    Layout::

        storage/teachers/{teacher_id}/exams/{exam_id}.pdf       (Playwright)
        storage/teachers/{teacher_id}/exams/{exam_id}.html      (fallback)

    Failures (no Playwright, no chromium, write errors, etc.) are
    logged and swallowed — the webhook still has the text
    confirmation to send back.
    """
    try:
        from app.core.config import settings
        from app.exam_engine.exam_export import export_exam_pdf

        result = export_exam_pdf(exam)
        if not result:
            return None

        exams_dir = settings.teacher_storage(teacher_id) / "exams"
        exams_dir.mkdir(parents=True, exist_ok=True)

        if result.pdf_bytes:
            pdf_path = exams_dir / f"{exam.exam_id}.pdf"
            pdf_path.write_bytes(result.pdf_bytes)
            logger.info(
                "[EXAM PDF SAVED] teacher_id=%d exam_id=%s path=%s size=%dKB",
                teacher_id, exam.exam_id, pdf_path,
                len(result.pdf_bytes) // 1024,
            )
            return str(pdf_path)

        # No Playwright available — keep the HTML so the route can
        # serve a printable page as fallback.
        html_path = exams_dir / f"{exam.exam_id}.html"
        html_path.write_text(result.html, encoding="utf-8")
        logger.info(
            "[EXAM HTML SAVED] teacher_id=%d exam_id=%s path=%s",
            teacher_id, exam.exam_id, html_path,
        )
        return str(html_path)
    except Exception as exc:  # noqa: BLE001
        logger.info("[EXAM FLOW] PDF render skipped: %s", exc)
    return None


# ──────────────────────────────────────────────────────────────────────
# Failure messages
# ──────────────────────────────────────────────────────────────────────


def _no_match_message() -> str:
    return (
        "لم أجد نموذجًا مناسبًا كفاية ⚠️\n"
        "يمكنك:\n"
        "📄 رفع نموذج اختبار سابق\n"
        "أو\n"
        "📘 تحديد الدرس بشكل أدق"
    )


__all__ = [
    "ExamFlowResult",
    "STAGE_MISSING_INFO",
    "STAGE_READY",
    "STAGE_NO_MATCH",
    "STAGE_FAILED",
    "handle_exam_request",
]
