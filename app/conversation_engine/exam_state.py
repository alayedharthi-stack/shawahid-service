"""
conversation_engine.exam_state — multi-turn exam conversation memory.

Phase-12 contract
=================
The teacher rarely supplies every exam slot in one message. We track
gathered slots across turns so the bot only ever asks about what's
*still* missing.

The store is in-memory, per-process, keyed by ``teacher_id``. State
expires after a quiet period — exactly like the rest of the
conversation memory store. Pure module: no DB / GPT / network.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


# ──────────────────────────────────────────────────────────────────────
# DTO
# ──────────────────────────────────────────────────────────────────────


@dataclass
class ExamConversationState:
    """All slots collected so far for the *current* exam request.

    Every field is optional. ``pending_fields`` carries the keys we
    still need to ask the teacher about so the webhook can build a
    short missing-info prompt instead of starting from zero.
    """

    teacher_id: int
    subject: str | None = None
    grade: str | None = None
    stage: str | None = None
    semester: str | None = None
    exam_type: str | None = None
    unit: str | None = None
    lesson: str | None = None
    week: int | None = None
    total_questions: int | None = None
    total_marks: int | None = None
    duration_minutes: int | None = None
    source_mode: str | None = None
    pending_fields: tuple[str, ...] = ()
    # ── Snapshot of the most-recent successful generation ─────────────
    last_exam_id: str | None = None
    last_pdf_path: str | None = None
    last_exam_subject: str | None = None
    last_exam_grade: str | None = None
    last_exam_type: str | None = None
    last_exam_download_url: str | None = None
    updated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    # ── Convenience ──────────────────────────────────────────────────

    def is_active(self) -> bool:
        """True when the teacher has at least one slot filled."""
        return any(
            getattr(self, k) for k in (
                "subject", "grade", "stage", "exam_type",
                "unit", "lesson", "semester",
            )
        )

    def merge(self, **slots) -> None:
        """Apply non-None slots and bump ``updated_at``."""
        for key, value in slots.items():
            if value is None:
                continue
            if hasattr(self, key):
                setattr(self, key, value)
        self.updated_at = datetime.now(timezone.utc)

    def reset(self) -> None:
        for key in (
            "subject", "grade", "stage", "semester", "exam_type",
            "unit", "lesson", "week", "total_questions", "total_marks",
            "duration_minutes", "source_mode", "last_exam_id",
            "last_pdf_path", "last_exam_subject", "last_exam_grade",
            "last_exam_type", "last_exam_download_url",
        ):
            setattr(self, key, None)
        self.pending_fields = ()
        self.updated_at = datetime.now(timezone.utc)

    @property
    def has_last_exam(self) -> bool:
        """True when a previously-generated exam is still cached."""
        return bool(self.last_exam_id)


# ──────────────────────────────────────────────────────────────────────
# Memory store
# ──────────────────────────────────────────────────────────────────────


_BACKEND: dict[int, ExamConversationState] = {}
_LOCK = threading.RLock()
_TTL = timedelta(minutes=30)


def get_exam_state(teacher_id: int) -> ExamConversationState:
    """Return the per-teacher exam conversation state, creating one
    if none exists or the existing one expired."""
    with _LOCK:
        st = _BACKEND.get(teacher_id)
        if st is None or _is_expired(st):
            st = ExamConversationState(teacher_id=teacher_id)
            _BACKEND[teacher_id] = st
        return st


def reset_exam_state(teacher_id: int) -> None:
    """Forget everything about ``teacher_id``'s current exam request."""
    with _LOCK:
        _BACKEND.pop(teacher_id, None)


def reset_all_exam_states() -> None:
    """Wipe the whole store. Used by tests only."""
    with _LOCK:
        _BACKEND.clear()


def merge_exam_state(teacher_id: int, **slots) -> ExamConversationState:
    """Apply slot updates to the per-teacher state."""
    with _LOCK:
        st = get_exam_state(teacher_id)
        st.merge(**slots)
        return st


def set_pending_fields(
    teacher_id: int,
    fields: tuple[str, ...],
) -> ExamConversationState:
    with _LOCK:
        st = get_exam_state(teacher_id)
        st.pending_fields = tuple(fields)
        st.updated_at = datetime.now(timezone.utc)
        return st


def record_generated_exam(
    teacher_id: int,
    *,
    exam_id: str,
    pdf_path: str | None,
    subject: str | None = None,
    grade: str | None = None,
    exam_type: str | None = None,
    download_url: str | None = None,
) -> ExamConversationState:
    """Snapshot the last successfully-generated exam.

    The webhook calls this immediately after ``handle_exam_request``
    returns ``STAGE_READY`` so that follow-up questions like
    "أين رابط الاختبار؟" can be answered without regenerating.
    """
    with _LOCK:
        st = get_exam_state(teacher_id)
        st.last_exam_id = exam_id
        st.last_pdf_path = pdf_path
        if subject:
            st.last_exam_subject = subject
        if grade:
            st.last_exam_grade = grade
        if exam_type:
            st.last_exam_type = exam_type
        if download_url:
            st.last_exam_download_url = download_url
        st.pending_fields = ()
        st.updated_at = datetime.now(timezone.utc)
        return st


def update_last_exam_download_url(
    teacher_id: int, *, download_url: str,
) -> ExamConversationState:
    """Late-set the download URL once the webhook has computed it.

    ``record_generated_exam`` runs inside the synchronous exam_flow which
    has no access to ``settings.effective_base_url`` — the webhook fills
    it in afterwards.
    """
    with _LOCK:
        st = get_exam_state(teacher_id)
        st.last_exam_download_url = download_url
        st.updated_at = datetime.now(timezone.utc)
        return st


# ──────────────────────────────────────────────────────────────────────
# Internals
# ──────────────────────────────────────────────────────────────────────


def _is_expired(st: ExamConversationState) -> bool:
    return (datetime.now(timezone.utc) - st.updated_at) > _TTL


__all__ = [
    "ExamConversationState",
    "get_exam_state",
    "reset_exam_state",
    "reset_all_exam_states",
    "merge_exam_state",
    "set_pending_fields",
    "record_generated_exam",
    "update_last_exam_download_url",
]
