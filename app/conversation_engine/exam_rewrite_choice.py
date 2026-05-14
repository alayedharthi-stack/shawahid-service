"""
conversation_engine.exam_rewrite_choice — Phase 2 pending-choice store.
───────────────────────────────────────────────────────────────────────

When a teacher uploads a PDF that the Phase-1 classifier judges to be
an exam / worksheet (``pdf_kind == "exam_or_worksheet"`` with
``confidence >= 0.70``) we MUST NOT save it as evidence automatically.
Instead we ask the teacher:

    1️⃣ حفظه كشاهد
    2️⃣ إعادة صياغة الاختبار

Until the teacher answers, the PDF metadata is parked here. The next
text message from the teacher is routed through ``parse_exam_choice``
to resume the right path.

Hard rules:
    • Per-process, in-memory store — same pattern as the other
      conversation_engine state modules. No DB / network.
    • Pure module: ``parse_exam_choice`` is a deterministic function.
    • TTL keeps stale parks from blocking later, unrelated messages.
    • Lives entirely inside ``shawahid-service`` and touches neither
      Nahla AI, campaigns, billing, subscriptions, catalog,
      coexistence, 360dialog, customer segmentation, nor any
      cross-service shared logic.
"""
from __future__ import annotations

import re
import threading
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal


# ──────────────────────────────────────────────────────────────────────
# DTO
# ──────────────────────────────────────────────────────────────────────


@dataclass
class PendingExamChoice:
    """Everything we need to resume either save or rewrite paths.

    The fields mirror the variables the webhook had in scope at the
    moment we decided to ask the teacher. Resuming on choice ``1``
    must work without re-downloading the file.
    """

    teacher_id: int
    storage_path: str | None = None
    file_name: str | None = None
    safe_filename: str | None = None
    mime_type: str | None = None
    media_url: str | None = None
    media_id: str | None = None
    media_hash: str | None = None
    detected_type: str | None = None  # exam | worksheet | assignment | assessment
    confidence: float = 0.0
    classifier_reason: str = ""
    extracted_text: str | None = None
    first_lines: str | None = None
    updated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc)


ChoiceKind = Literal["save", "rewrite"]


# ──────────────────────────────────────────────────────────────────────
# Store (per-process, in-memory, TTL'd)
# ──────────────────────────────────────────────────────────────────────


_BACKEND: dict[int, PendingExamChoice] = {}
_LOCK = threading.RLock()
_TTL = timedelta(minutes=15)


def set_pending(
    teacher_id: int,
    *,
    storage_path: str | None = None,
    file_name: str | None = None,
    safe_filename: str | None = None,
    mime_type: str | None = None,
    media_url: str | None = None,
    media_id: str | None = None,
    media_hash: str | None = None,
    detected_type: str | None = None,
    confidence: float = 0.0,
    classifier_reason: str = "",
    extracted_text: str | None = None,
    first_lines: str | None = None,
) -> PendingExamChoice:
    """Park the file metadata while waiting for the teacher's choice.

    Always overwrites any previously parked choice for ``teacher_id``
    — a fresh PDF supersedes whatever was waiting.
    """
    with _LOCK:
        entry = PendingExamChoice(
            teacher_id=teacher_id,
            storage_path=storage_path,
            file_name=file_name,
            safe_filename=safe_filename,
            mime_type=mime_type,
            media_url=media_url,
            media_id=media_id,
            media_hash=media_hash,
            detected_type=detected_type,
            confidence=float(confidence or 0.0),
            classifier_reason=classifier_reason or "",
            extracted_text=extracted_text,
            first_lines=first_lines,
        )
        _BACKEND[teacher_id] = entry
        return entry


def get_pending(teacher_id: int) -> PendingExamChoice | None:
    """Return the parked choice (or ``None``) and drop it if expired."""
    with _LOCK:
        entry = _BACKEND.get(teacher_id)
        if entry is None:
            return None
        if _is_expired(entry):
            _BACKEND.pop(teacher_id, None)
            return None
        return entry


def has_pending(teacher_id: int) -> bool:
    return get_pending(teacher_id) is not None


def clear_pending(teacher_id: int) -> None:
    with _LOCK:
        _BACKEND.pop(teacher_id, None)


def reset_all() -> None:
    """Wipe the whole store. Used by tests only."""
    with _LOCK:
        _BACKEND.clear()


def _is_expired(entry: PendingExamChoice) -> bool:
    return (datetime.now(timezone.utc) - entry.updated_at) > _TTL


# ──────────────────────────────────────────────────────────────────────
# Choice parser
# ──────────────────────────────────────────────────────────────────────


_ARABIC_DIACRITICS = re.compile(r"[\u064B-\u065F\u0670]")

# Save-as-evidence triggers — option 1.
_SAVE_TOKENS: tuple[str, ...] = (
    "1",
    "١",
    "1️⃣",
    "اول",
    "الاول",
    "خيار 1",
    "الخيار 1",
    "خيار اول",
    "الخيار الاول",
    "حفظ",
    "حفظه",
    "احفظه",
    "احفظ",
    "حفظه كشاهد",
    "احفظه كشاهد",
    "كشاهد",
    "خله شاهد",
    "خلها شاهد",
    "اعتمده كشاهد",
    "خزنه",
    "خزنه شاهد",
)

# Rewrite-the-exam triggers — option 2.
_REWRITE_TOKENS: tuple[str, ...] = (
    "2",
    "٢",
    "2️⃣",
    "ثاني",
    "الثاني",
    "الثانيه",
    "ثانيا",
    "خيار 2",
    "الخيار 2",
    "خيار ثاني",
    "الخيار الثاني",
    "اعد صياغه",
    "اعد صياغته",
    "اعاده صياغه",
    "اعاده الصياغه",
    "اعاده صياغت",
    "اعاده صياغة",
    "صياغه",
    "صياغة",
    "اعد الصياغه",
    "اعد الصياغة",
    "اعاده صياغه الاختبار",
    "كليشه المدرسه",
    "كليشة المدرسة",
    "بكليشه المدرسه",
    "بكليشة المدرسة",
    "حطه بكليشه",
    "حطه بكليشة",
    "نظفه",
    "نظفه من الاسماء",
    "رتبه",
    "سو لي نسخه",
    "سو لي نسخة",
    "نسخه جديده",
    "نسخة جديدة",
)


def _fold(text: str) -> str:
    """Aggressive Arabic folding strictly for matching.

    Keep the helper local — we deliberately do NOT pull in
    ``app.services.intents.normalize`` because that module knows about
    business intents we don't want to entangle here.
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = _ARABIC_DIACRITICS.sub("", text)
    text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    text = text.replace("ى", "ي").replace("ئ", "ي").replace("ؤ", "و")
    text = text.replace("ة", "ه")
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def parse_exam_choice(text: str | None) -> ChoiceKind | None:
    """Return ``"save"`` / ``"rewrite"`` / ``None`` for a teacher reply.

    The parser is intentionally strict: a reply that mentions nothing
    decisive (e.g. a long unrelated sentence) returns ``None`` so the
    webhook can fall through to its normal GPT path.

    If a reply could equally mean both choices (e.g. an emoji 1 plus
    the word "صياغة"), the rewrite branch wins because that is the
    irreversible-looking decision we'd rather over-clarify than
    silently treat as a save.
    """
    if not text:
        return None
    folded = _fold(text)
    if not folded:
        return None

    has_save = _matches_any(folded, _SAVE_TOKENS)
    has_rewrite = _matches_any(folded, _REWRITE_TOKENS)

    if has_rewrite and has_save:
        # Ambiguous — bias to the more consequential action.
        return "rewrite"
    if has_save:
        return "save"
    if has_rewrite:
        return "rewrite"
    return None


def _matches_any(folded_text: str, tokens: tuple[str, ...]) -> bool:
    # Tokens are stored in their raw "user input" form; fold them once
    # at lookup so we can compare apples to apples.
    for raw in tokens:
        token = _fold(raw)
        if not token:
            continue
        # Single-character / digit tokens must match the *whole* reply
        # (or a leading token), otherwise "12" or "12 مساءً" would
        # falsely pass.
        if len(token) <= 2:
            if folded_text == token:
                return True
            # Match "1 ", "1." "1)" at the start.
            if re.match(rf"^{re.escape(token)}\b", folded_text):
                return True
            continue
        if token in folded_text:
            return True
    return False


__all__ = [
    "PendingExamChoice",
    "ChoiceKind",
    "set_pending",
    "get_pending",
    "has_pending",
    "clear_pending",
    "reset_all",
    "parse_exam_choice",
]
