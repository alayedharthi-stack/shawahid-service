"""
exam_engine.sources.local_samples — bundled, hand-written sample bank.

This is the ONLY source that returns real content during phase-10.
The samples are tiny, deliberately generic, and only used for:

    1. integration / smoke testing of the generator
    2. fallback when ``source_mode = SOURCE_SAMPLE_BANK`` and no
       teacher file is available
    3. demonstrating the question-types matrix to teachers

External providers (madati / kutubi / manhaji) remain placeholders
until the legal-and-quality review described in ``base.py`` lands.
"""
from __future__ import annotations

from typing import Iterable

from app.exam_engine.schemas import (
    QTYPE_FILL_BLANK,
    QTYPE_MCQ,
    QTYPE_SHORT,
    QTYPE_TRUE_FALSE,
    ExamQuestion,
)
from app.exam_engine.sources.base import (
    ExamSourceProvider,
    QualityReport,
    SourceQuery,
    SourceSample,
)


# ──────────────────────────────────────────────────────────────────────
# Hand-written sample bank — keep tiny and generic.
# Real, curriculum-bound banks come in a later phase.
# ──────────────────────────────────────────────────────────────────────


def _id(prefix: str, n: int) -> str:
    return f"local-{prefix}-{n}"


_MATH_PRIMARY: tuple[ExamQuestion, ...] = (
    ExamQuestion(
        id=_id("math-p", 1),
        type=QTYPE_MCQ,
        text="ناتج جمع 12 + 8 يساوي:",
        choices=("18", "20", "22", "24"),
        correct_answer=1,
        marks=1,
        difficulty="easy",
        learning_outcome="يجمع الطالب أعدادًا ضمن 50",
        bloom_level="application",
    ),
    ExamQuestion(
        id=_id("math-p", 2),
        type=QTYPE_TRUE_FALSE,
        text="العدد 9 أكبر من العدد 7.",
        choices=(),
        correct_answer="صح",
        marks=1,
        difficulty="easy",
        learning_outcome="يقارن الطالب بين عددين",
        bloom_level="comprehension",
    ),
    ExamQuestion(
        id=_id("math-p", 3),
        type=QTYPE_FILL_BLANK,
        text="ضعف العدد 6 يساوي ............",
        correct_answer="12",
        marks=2,
        difficulty="medium",
        learning_outcome="يحسب الطالب ضعف عدد",
        bloom_level="application",
    ),
)


_SCIENCE_PRIMARY: tuple[ExamQuestion, ...] = (
    ExamQuestion(
        id=_id("sci-p", 1),
        type=QTYPE_MCQ,
        text="من حالات المادة:",
        choices=("الحرارة فقط", "الصلب والسائل والغاز", "الضوء فقط", "الصوت فقط"),
        correct_answer=1,
        marks=1,
        difficulty="easy",
        learning_outcome="يصف الطالب حالات المادة",
        bloom_level="knowledge",
    ),
    ExamQuestion(
        id=_id("sci-p", 2),
        type=QTYPE_TRUE_FALSE,
        text="الماء يتبخر عند تسخينه.",
        correct_answer="صح",
        marks=1,
        difficulty="easy",
        learning_outcome="يفسر الطالب تحول الماء",
        bloom_level="comprehension",
    ),
    ExamQuestion(
        id=_id("sci-p", 3),
        type=QTYPE_SHORT,
        text="اذكر مثالًا لمصدر طبيعي للضوء.",
        correct_answer="الشمس",
        marks=2,
        difficulty="medium",
        learning_outcome="يميز الطالب بين مصادر الضوء",
        bloom_level="comprehension",
    ),
)


# Map (subject, stage) → list of questions. Keys are kept tolerant —
# the lookup is "best-effort", not strict.
_BANK: dict[tuple[str, str], tuple[ExamQuestion, ...]] = {
    ("الرياضيات", "primary"): _MATH_PRIMARY,
    ("الرياضيات", "المرحلة الابتدائية"): _MATH_PRIMARY,
    ("العلوم", "primary"): _SCIENCE_PRIMARY,
    ("العلوم", "المرحلة الابتدائية"): _SCIENCE_PRIMARY,
}


def lookup_sample_questions(
    *,
    subject: str | None,
    stage: str | None,
) -> tuple[ExamQuestion, ...]:
    """Return the bundled sample questions for ``(subject, stage)``.

    Returns an empty tuple when there is no bundled match. Caller
    decides what to do (typically: ask the teacher to upload a
    sample, or fall back to a prompt-builder template).
    """
    if not subject:
        return ()
    if stage:
        bucket = _BANK.get((subject, stage))
        if bucket:
            return bucket
    # Try any stage match for the subject as a soft fallback.
    for (subj, _stg), bucket in _BANK.items():
        if subj == subject:
            return bucket
    return ()


# ──────────────────────────────────────────────────────────────────────
# Provider façade
# ──────────────────────────────────────────────────────────────────────


class LocalSamplesProvider(ExamSourceProvider):
    """Bundled, hand-written sample provider. Always safe to call."""

    name = "local_samples"
    source_url = "(bundled)"

    @property
    def supports_subjects(self) -> tuple[str, ...]:
        return tuple({k[0] for k in _BANK})

    @property
    def supports_stages(self) -> tuple[str, ...]:
        return tuple({k[1] for k in _BANK})

    supports_exam_types = ()  # any exam type — we just supply questions

    def fetch(self, query: SourceQuery) -> Iterable[SourceSample]:
        questions = lookup_sample_questions(
            subject=query.subject, stage=query.stage,
        )
        if not questions:
            return ()

        # Phase-11: emit a JSON ``raw_content`` so the shared
        # ``normalize_exam_source`` step in the pipeline can parse the
        # bundled sample with the same code path as external providers.
        import json
        payload = json.dumps({
            "title": f"اختبار قصير - {query.subject}",
            "meta": {
                "subject": query.subject,
                "stage": query.stage,
                "semester": query.semester,
                "exam_type": query.exam_type,
            },
            "questions": [
                {
                    "text": q.text,
                    "type": q.type,
                    "choices": list(q.choices),
                    "correct_answer": q.correct_answer,
                    "marks": q.marks,
                    "difficulty": q.difficulty,
                    "learning_outcome": q.learning_outcome,
                }
                for q in questions
            ],
        })

        return (SourceSample(
            provider=self.name,
            title=f"local-{query.subject}-{query.stage}",
            raw_content=payload,
            metadata={
                "question_ids": tuple(q.id for q in questions),
                "subject": query.subject,
                "stage": query.stage,
            },
        ),)

    def normalize(self, sample: SourceSample) -> SourceSample:
        return sample  # already normalised

    def extract_questions(
        self, sample: SourceSample
    ) -> tuple[ExamQuestion, ...]:
        ids = sample.metadata.get("question_ids", ())
        subject = sample.metadata.get("subject")
        stage = sample.metadata.get("stage")
        questions = lookup_sample_questions(subject=subject, stage=stage)
        return tuple(q for q in questions if q.id in ids) or questions

    def quality_check(
        self, sample: SourceSample, questions: tuple[ExamQuestion, ...]
    ) -> QualityReport:
        if not questions:
            return QualityReport(False, "no questions", ("empty",))
        # Bundled content is hand-curated → always acceptable.
        return QualityReport(True, "bundled sample bank")


__all__ = [
    "LocalSamplesProvider",
    "lookup_sample_questions",
]
