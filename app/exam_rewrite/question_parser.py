"""
exam_rewrite.question_parser — split cleaned exam text into sections,
questions, and answer choices.

Approach
--------
Walk the cleaned lines top-to-bottom. We maintain three rolling
buffers — current section, current question, and current question's
choices — and flush them on the next structural marker (new section
or new question).

Phase-3 hard rules:
    • Pure function — no GPT, no DB, no network.
    • Deterministic: same input → same output.
    • Conservative: when in doubt we attach a line to the current
      question text instead of inventing a new question. Worst case
      is one slightly-too-long question — never a phantom one.

Question type is inferred from the text + presence of choices using
small Arabic keyword banks. The unknown bucket exists precisely so we
don't fabricate a type when the signals aren't there.
"""
from __future__ import annotations

import re
import unicodedata

from app.exam_rewrite.schemas import (
    ExamQuestion,
    ExamSection,
    QUESTION_TYPE_COMPLETE,
    QUESTION_TYPE_ESSAY,
    QUESTION_TYPE_MATCHING,
    QUESTION_TYPE_MULTIPLE_CHOICE,
    QUESTION_TYPE_SHORT_ANSWER,
    QUESTION_TYPE_TRUE_FALSE,
    QUESTION_TYPE_UNKNOWN,
)


# ──────────────────────────────────────────────────────────────────────
# Folding & digit normalisation (matching only)
# ──────────────────────────────────────────────────────────────────────

_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def _fold(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[\u064B-\u065F\u0670]", "", text)
    text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    text = text.replace("ى", "ي").replace("ئ", "ي").replace("ؤ", "و")
    text = text.replace("ة", "ه")
    return re.sub(r"\s+", " ", text).strip().lower()


# ──────────────────────────────────────────────────────────────────────
# Structural regex
# ──────────────────────────────────────────────────────────────────────


# Section header (matched against folded text — note ؤ→و so "السؤال"
# becomes "السوال"). Two-word ordinals come BEFORE single-word ones so
# "الثاني عشر" wins over "الثاني".
_ORDINALS_RE = (
    r"("
    r"الحادي\s*عشر|الثاني\s*عشر|"
    r"الاولي|الاول|الثاني|الثالث|الرابع|الخامس|"
    r"السادس|السابع|الثامن|التاسع|العاشر|"
    r"\d{1,2}"
    r")"
)

_SECTION_HEADER_FOLDED_RE = re.compile(
    rf"^(?:السوال|القسم|المجال|الجزء)\s+{_ORDINALS_RE}(?:\b|(?=\s|[:.\-,]|$))"
    r"[\s:.\-]*(.*)$"
)


# Question marker (folded): "1) ...", "1- ...", "1. ...", "س1: ...",
# "س ١-", and "(1) ...". Arabic digits are pre-converted to Latin by
# the caller before applying this regex.
_QUESTION_LINE_FOLDED_RE = re.compile(
    r"^(?:س\s*)?\(?(\d{1,2})\)?\s*[\)\.\-:]\s*(.*)$"
)


# Choice on its own line: "أ) ..." / "ب- ..." / "A) ...".
_CHOICE_LINE_RE = re.compile(
    r"^\s*([أ-دa-d])\s*[\)\.\-:]\s*(.+)$",
    flags=re.IGNORECASE,
)


# Inline choice splitter — used when one line carries all the choices.
# We require at least two markers to avoid false positives on plain
# Arabic words that happen to start with أ / ب.
_INLINE_CHOICE_RE = re.compile(
    r"(?:^|\s)([أ-دa-d])\s*[\)\.\-:]\s*",
    flags=re.IGNORECASE,
)


# Per-line score extraction.
_INLINE_SCORE_RE = re.compile(
    r"\(\s*(\d{1,3}(?:\.\d{1,2})?)\s*(?:درجه|درجات|نقطه|نقاط|درجة)?\s*\)"
)
_TRAILING_SCORE_RE = re.compile(
    r"[/\\\-]\s*(\d{1,3}(?:\.\d{1,2})?)\s*$"
)


# Question-type detection keyword banks (folded).
_TF_PATTERNS_RE = re.compile(
    r"(?:صح\s*(?:ام|او|و)?\s*خطا|"
    r"صحيح\s*(?:ام|او|و)?\s*خطا|"
    r"ضع\s*علامه\s*\(?\s*[√✓×x✗-]\s*\)?|"
    r"ضع\s*علامه\s*صح)"
)
_COMPLETE_RE = re.compile(
    r"(?:اكمل\s*(?:الفراغ|ما\s*يلي|الفراغات|الجدول)|"
    r"املا\s*الفراغ|املا\s*ما\s*يلي|اكتب\s*الكلمه\s*المناسبه|"
    r"\bالفراغ\b|"
    # A long run of dots / underscores in a question body is a clear
    # complete-the-blank signal even when the prompt doesn't say so.
    r"\.{4,}|"
    r"_{4,})"
)
_MATCHING_RE = re.compile(
    r"(?:صل\s*بين|وصل\s*بين|اوصل\s*بين)"
)
_ESSAY_RE = re.compile(
    r"(?:اكتب\s*فقره|اكتب\s*موضوع|ناقش|اشرح|علل|عرف|اكتب\s*مقالا)"
)
_SHORT_ANSWER_RE = re.compile(
    # ``\b`` ensures "عدد" does NOT match the noun "العدد" inside a
    # true/false statement like "العدد 7 عدد فردي".
    r"\b(?:اجب|اجابه\s*قصيره|ما\s*هو|ما\s*هي|اذكر|عدد|عرف|قارن)\b"
)
_MCQ_HINT_RE = re.compile(
    r"(?:اختر\s*(?:الاجابه|الاجابات|من\s*متعدد)|"
    r"اختيار\s*من\s*متعدد|الاجابه\s*الصحيحه)"
)


# ──────────────────────────────────────────────────────────────────────
# Internal mutable buffers
# ──────────────────────────────────────────────────────────────────────


class _QBuf:
    """Mutable buffer for an in-progress question (private)."""

    __slots__ = ("number", "text_lines", "choices", "score")

    def __init__(self, number: int) -> None:
        self.number: int = number
        self.text_lines: list[str] = []
        self.choices: list[str] = []
        self.score: float | None = None

    def add_text(self, line: str) -> None:
        line = line.strip()
        if line:
            self.text_lines.append(line)

    def add_choice(self, choice: str) -> None:
        choice = choice.strip().rstrip("., -")
        if choice:
            self.choices.append(choice)

    def joined_text(self) -> str:
        return " ".join(self.text_lines).strip()

    def to_question(self) -> ExamQuestion:
        text = self.joined_text()
        choices = tuple(self.choices)
        qtype = _classify_question_type(text, choices)
        return ExamQuestion(
            number=self.number,
            type=qtype,
            text=text,
            choices=choices,
            score=self.score,
        )


class _SBuf:
    """Mutable buffer for an in-progress section."""

    __slots__ = ("title", "score", "questions")

    def __init__(self, title: str | None = None, score: float | None = None) -> None:
        self.title: str | None = title
        self.score: float | None = score
        self.questions: list[_QBuf] = []

    def to_section(self) -> ExamSection:
        # The section header is the strongest signal for question type
        # ("السؤال الثاني: ضع علامة صح أو خطأ" → every question inside
        # is true/false). When present we let it override the
        # per-question heuristic — *except* for MCQ questions that
        # already carry their own choices, because choices are an even
        # stronger signal than the header.
        hint = _section_type_hint(self.title)
        questions: list[ExamQuestion] = []
        for qbuf in self.questions:
            q = qbuf.to_question()
            if hint is not None and not (
                q.type == QUESTION_TYPE_MULTIPLE_CHOICE
                and len(q.choices) >= 2
            ):
                if q.type != hint:
                    q = ExamQuestion(
                        number=q.number,
                        type=hint,
                        text=q.text,
                        choices=q.choices,
                        score=q.score,
                    )
            questions.append(q)
        return ExamSection(
            title=self.title,
            score=self.score,
            questions=tuple(questions),
        )


def _section_type_hint(section_title: str | None) -> str | None:
    """Infer a default question type from a section header."""
    if not section_title:
        return None
    folded = _fold(section_title).translate(_AR_DIGITS)
    if _TF_PATTERNS_RE.search(folded):
        return QUESTION_TYPE_TRUE_FALSE
    if _MCQ_HINT_RE.search(folded):
        return QUESTION_TYPE_MULTIPLE_CHOICE
    if _COMPLETE_RE.search(folded):
        return QUESTION_TYPE_COMPLETE
    if _MATCHING_RE.search(folded):
        return QUESTION_TYPE_MATCHING
    if _ESSAY_RE.search(folded):
        return QUESTION_TYPE_ESSAY
    if _SHORT_ANSWER_RE.search(folded):
        return QUESTION_TYPE_SHORT_ANSWER
    return None


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────


def parse_sections(cleaned_lines: list[str]) -> list[ExamSection]:
    """Walk cleaned lines and return sections with parsed questions.

    Lines must already be free of page markers and signature
    boilerplate (see :mod:`text_cleaner`). The function:
        1. Identifies explicit section headers ("السؤال الأول").
        2. Falls back to a single anonymous section when none exist.
        3. Inside each section, splits into numbered questions.
        4. Attaches inline / multi-line answer choices.
        5. Detects per-question and per-section scores.
        6. Tags each question with one of the seven types.
    """
    if not cleaned_lines:
        return []

    sections: list[_SBuf] = []
    current_section: _SBuf | None = None
    current_question: _QBuf | None = None

    def _ensure_section() -> _SBuf:
        nonlocal current_section
        if current_section is None:
            current_section = _SBuf(title=None, score=None)
            sections.append(current_section)
        return current_section

    def _flush_question() -> None:
        nonlocal current_question
        if current_question is None:
            return
        _ensure_section().questions.append(current_question)
        current_question = None

    for raw_line in cleaned_lines:
        line = raw_line.strip()
        if not line:
            continue
        folded = _fold(line).translate(_AR_DIGITS)
        if not folded:
            continue

        # ── 1. Section header? ───────────────────────────────────────
        sec_match = _SECTION_HEADER_FOLDED_RE.match(folded)
        if sec_match:
            _flush_question()
            # Close the previous (anonymous) section ONLY if it has
            # questions — otherwise it's an empty placeholder we can
            # repurpose by giving it a title.
            if current_section is not None and not current_section.questions:
                # Re-title the empty section.
                current_section.title = line
                current_section.score = _maybe_score(line)
            else:
                current_section = _SBuf(
                    title=line,
                    score=_maybe_score(line),
                )
                sections.append(current_section)
            continue

        # ── 2. Question marker? ─────────────────────────────────────
        # Only treat a line as a new question when:
        #   • it begins with a number marker AND
        #   • the number is plausibly the *next* question (≤ current+1
        #     or starts fresh after a section header).
        q_match = _QUESTION_LINE_FOLDED_RE.match(folded)
        if q_match:
            try:
                q_num = int(q_match.group(1))
            except ValueError:
                q_num = -1
            if 1 <= q_num <= 99 and _looks_like_new_question(
                current_question, current_section, q_num,
            ):
                _flush_question()
                # Capture the part of the *original* line after the
                # marker so we keep Arabic letters / casing intact.
                body = _strip_leading_marker(line)
                qbuf = _QBuf(number=q_num)
                inline_choices = _extract_inline_choices(body)
                if inline_choices:
                    text_part = body[: _first_inline_choice_pos(body)].strip()
                    if text_part:
                        qbuf.add_text(text_part)
                    for ch in inline_choices:
                        qbuf.add_choice(ch)
                else:
                    qbuf.add_text(body)
                qbuf.score = _maybe_score(line)
                current_question = qbuf
                continue

        # ── 3. Standalone choice line for the current question? ─────
        ch_match = _CHOICE_LINE_RE.match(line)
        if ch_match and current_question is not None:
            current_question.add_choice(ch_match.group(2))
            continue

        # ── 4. Inline-multi-choice line for current question? ───────
        inline = _extract_inline_choices(line)
        if inline and current_question is not None and len(inline) >= 2:
            for ch in inline:
                current_question.add_choice(ch)
            continue

        # ── 5. Plain continuation of the current question text ──────
        if current_question is not None:
            current_question.add_text(line)
            continue

        # ── 6. Free-floating preamble — ignore. The metadata
        # extractor uses these lines separately for title / etc. ────
        continue

    _flush_question()

    # Drop empty trailing sections (e.g. headers with no body).
    return [s.to_section() for s in sections if s.questions]


def _looks_like_new_question(
    current_q: _QBuf | None,
    current_s: _SBuf | None,
    n: int,
) -> bool:
    """Reject question markers that look like in-text references.

    Acceptable when:
        • no current question yet, OR
        • the new number is exactly current_question.number + 1, OR
        • the line is the first question after a (re-)titled section.
    """
    if current_q is None:
        return True
    if n == current_q.number + 1:
        return True
    if current_s is not None and current_s.title and not current_s.questions:
        return True
    # Restart-from-1 after a section header is also legitimate.
    if n == 1 and current_s is not None and not current_s.questions:
        return True
    return False


def _strip_leading_marker(line: str) -> str:
    """Drop the leading ``"1) "`` / ``"س1: "`` from ``line``."""
    return re.sub(
        r"^\s*(?:س\s*)?\(?[\d\u0660-\u0669]{1,2}\)?\s*[\)\.\-:]\s*",
        "",
        line,
    ).strip()


def _extract_inline_choices(line: str) -> list[str] | None:
    matches = list(_INLINE_CHOICE_RE.finditer(line))
    if len(matches) < 2:
        return None
    choices: list[str] = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(line)
        chunk = line[start:end].strip(" \t,،.;-")
        if chunk:
            choices.append(chunk)
    return choices or None


def _first_inline_choice_pos(line: str) -> int:
    m = _INLINE_CHOICE_RE.search(line)
    return m.start() if m else len(line)


def _maybe_score(line: str) -> float | None:
    folded = _fold(line).translate(_AR_DIGITS)
    m = _INLINE_SCORE_RE.search(folded)
    if m:
        try:
            score = float(m.group(1))
        except ValueError:
            return None
        if 0 < score <= 200:
            return score
    m = _TRAILING_SCORE_RE.search(folded)
    if m:
        try:
            score = float(m.group(1))
        except ValueError:
            return None
        if 0 < score <= 200:
            return score
    return None


def _classify_question_type(text: str, choices: tuple[str, ...]) -> str:
    """Map free-form text + choices to one of the seven types."""
    folded = _fold(text).translate(_AR_DIGITS)
    has_choices = len(choices) >= 2

    if _TF_PATTERNS_RE.search(folded):
        return QUESTION_TYPE_TRUE_FALSE
    # MCQ hint OR has 3+ short choices.
    if _MCQ_HINT_RE.search(folded) or len(choices) >= 3:
        return QUESTION_TYPE_MULTIPLE_CHOICE
    if _COMPLETE_RE.search(folded):
        return QUESTION_TYPE_COMPLETE
    if _MATCHING_RE.search(folded):
        return QUESTION_TYPE_MATCHING
    if _ESSAY_RE.search(folded):
        return QUESTION_TYPE_ESSAY
    if _SHORT_ANSWER_RE.search(folded):
        return QUESTION_TYPE_SHORT_ANSWER
    if has_choices:
        return QUESTION_TYPE_MULTIPLE_CHOICE
    if not folded:
        return QUESTION_TYPE_UNKNOWN
    return QUESTION_TYPE_UNKNOWN


__all__ = ["parse_sections"]
