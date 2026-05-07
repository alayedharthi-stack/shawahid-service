"""
Internal helper: shared scaffold for external providers.

``MadatiProvider`` / ``KutubiProvider`` / ``ManhajiProvider`` all do
the same thing — issue HTTP GETs through the pluggable client, parse
the body via ``normalize_exam_source``, and turn the candidate
questions into ``ExamQuestion`` instances. The only differences are:

    • the URL templates per provider
    • what the provider declares it supports
    • which test pages the in-memory client will return

Putting the scaffold here keeps each provider file tiny and avoids
copy-paste drift.
"""
from __future__ import annotations

import logging
from typing import Iterable

from app.exam_engine.schemas import (
    QTYPE_MCQ,
    QTYPE_SHORT,
    QTYPES_ALL,
    ExamQuestion,
)
from app.exam_engine.sources.base import (
    ExamSourceProvider,
    QualityReport,
    SourceQuery,
    SourceSample,
)
from app.exam_engine.sources.http_client import HttpClient, default_client
from app.exam_engine.sources.source_normalizer import (
    NormalizedSample,
    normalize_exam_source,
)
from app.exam_engine.sources.source_quality import check_sample_quality

logger = logging.getLogger(__name__)


class ExternalProviderBase(ExamSourceProvider):
    """Concrete-provider scaffold shared by external HTTP-based providers."""

    name: str = ""
    source_url: str = ""

    # URL templates the provider knows about. Subclasses override.
    # Each template can use ``{subject}`` / ``{grade}`` / ``{stage}``
    # / ``{semester}`` / ``{exam_type}`` placeholders.
    url_templates: tuple[str, ...] = ()

    # How many URLs to try at most per query.
    max_urls: int = 3

    # Default request timeout (seconds).
    request_timeout: float = 8.0

    def __init__(self, *, http_client: HttpClient | None = None) -> None:
        self.http: HttpClient = http_client or default_client()

    # ── fetch ─────────────────────────────────────────────────────────

    def fetch(self, query: SourceQuery) -> Iterable[SourceSample]:
        """Issue HTTP GETs and yield raw samples. Fail-soft."""
        if not self.url_templates:
            return ()
        out: list[SourceSample] = []
        for template in self.url_templates[: self.max_urls]:
            url = self._format_url(template, query)
            try:
                resp = self.http.get(url, timeout=self.request_timeout)
            except Exception as exc:  # noqa: BLE001
                logger.info("[%s] http.get failed for %s: %s", self.name, url, exc)
                continue
            if resp is None or not resp.ok or not (resp.body or "").strip():
                continue
            out.append(SourceSample(
                provider=self.name,
                title=f"{self.name}-{query.subject or 'all'}",
                raw_content=resp.body,
                metadata={
                    "url": url,
                    "subject": query.subject,
                    "grade": query.grade,
                    "stage": query.stage,
                    "semester": query.semester,
                    "exam_type": query.exam_type,
                },
            ))
        return tuple(out)

    # ── normalize ─────────────────────────────────────────────────────

    def normalize(self, sample: SourceSample) -> SourceSample:
        """No-op at this layer — heavy lifting happens inside
        ``normalize_exam_source`` invoked by the pipeline."""
        return sample

    # ── extract_questions ─────────────────────────────────────────────

    def extract_questions(
        self, sample: SourceSample,
    ) -> tuple[ExamQuestion, ...]:
        """Map normalized candidates → ``ExamQuestion`` DTOs."""
        normalized: NormalizedSample = normalize_exam_source(sample)
        if not normalized.questions:
            return ()
        out: list[ExamQuestion] = []
        for c in normalized.questions:
            if c.type not in QTYPES_ALL:
                continue
            out.append(ExamQuestion(
                id=ExamQuestion.new_id(),
                type=c.type,
                text=c.text,
                choices=c.choices,
                correct_answer=c.correct_answer or "",
                marks=max(0.5, c.marks),
                difficulty=c.difficulty or "medium",
                learning_outcome=c.learning_outcome,
            ))
        return tuple(out)

    # ── quality_check ─────────────────────────────────────────────────

    def quality_check(
        self, sample: SourceSample,
        questions: tuple[ExamQuestion, ...],
    ) -> QualityReport:
        normalized = normalize_exam_source(sample)
        return check_sample_quality(normalized)

    # ── helpers ───────────────────────────────────────────────────────

    def _format_url(self, template: str, query: SourceQuery) -> str:
        return template.format(
            subject=query.subject or "",
            grade=query.grade or "",
            stage=query.stage or "",
            semester=query.semester or "",
            exam_type=query.exam_type or "",
            unit=query.unit or "",
            lesson=query.lesson or "",
        )


__all__ = ["ExternalProviderBase"]
