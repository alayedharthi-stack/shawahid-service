"""
exam_engine.sources.base — abstract source-provider interface.

Phase-11 contract
=================
Every concrete provider goes through the unified pipeline:

    fetch → normalize → extract questions → quality check
          → deduplicate → adapt (anti-copy) → exam

Concrete providers may now perform real fetches, but they MUST:

    1. respect robots.txt and the site's terms of service
    2. use the pluggable ``HttpClient`` (default: ``DisabledHttpClient``)
    3. wrap network IO in timeouts + retries (handled by RequestsHttpClient)
    4. pass content through ``quality_check`` before exposing
    5. NEVER copy a full sample sheet verbatim — anti_copy must run

Operators activate real network access by setting
``EXAM_SOURCES_NETWORK_ENABLED=1`` AND injecting a real HttpClient.
By default everything stays offline.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterable

from app.exam_engine.schemas import ExamQuestion


# ──────────────────────────────────────────────────────────────────────
# Source DTOs
# ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SourceQuery:
    """A normalised query the engine sends to a provider."""

    subject: str | None = None
    grade: str | None = None
    stage: str | None = None
    semester: str | None = None
    exam_type: str | None = None
    unit: str | None = None
    lesson: str | None = None


@dataclass(frozen=True)
class SourceSample:
    """A raw sample returned by a provider before normalisation.

    Concrete providers return one of these per discovered exam paper.
    The engine then runs ``normalize_sample`` to convert it into the
    project's ``ExamQuestion`` shape.
    """

    provider: str
    title: str
    raw_content: str
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class QualityReport:
    """Outcome of running a sample through ``quality_check``."""

    is_acceptable: bool
    reason: str = ""
    flags: tuple[str, ...] = ()


# ──────────────────────────────────────────────────────────────────────
# Abstract provider
# ──────────────────────────────────────────────────────────────────────


class ExamSourceProvider(ABC):
    """Abstract provider — every concrete source subclasses this.

    The interface intentionally separates the four concerns the user
    listed in the phase brief:

        ``fetch``            → pull samples (network IO, future phase)
        ``normalize``        → coerce to ``SourceSample`` instances
        ``extract_questions``→ map samples → ``ExamQuestion`` DTOs
        ``quality_check``    → reject low-quality / off-topic samples
    """

    name: str = ""
    source_url: str = ""

    # ── Phase-10: every provider declares the surface area it
    #     supports. The engine uses this to rank providers per query.
    supports_subjects: tuple[str, ...] = ()
    supports_stages: tuple[str, ...] = ()
    supports_exam_types: tuple[str, ...] = ()

    # ── Override per concrete provider ─────────────────────────────────

    @abstractmethod
    def fetch(self, query: SourceQuery) -> Iterable[SourceSample]:
        """Return raw samples matching ``query``.

        Concrete providers MUST be fail-soft: any network error /
        parsing error returns an empty iterable instead of raising.
        The pipeline isolates each provider so a single bad provider
        cannot bring down the whole engine.
        """

    @abstractmethod
    def normalize(self, sample: SourceSample) -> SourceSample:
        """Strip boilerplate, fix encoding, coerce metadata."""

    @abstractmethod
    def extract_questions(
        self, sample: SourceSample
    ) -> tuple[ExamQuestion, ...]:
        """Convert a normalised sample into project-shape questions."""

    @abstractmethod
    def quality_check(
        self, sample: SourceSample, questions: tuple[ExamQuestion, ...]
    ) -> QualityReport:
        """Decide whether the sample should be exposed downstream."""

    # ── Convenience defaults usable by sub-classes ─────────────────────

    def can_handle(self, query: SourceQuery) -> bool:
        """Cheap pre-filter — does this provider claim to cover the query?"""
        if query.subject and self.supports_subjects:
            if query.subject not in self.supports_subjects:
                return False
        if query.stage and self.supports_stages:
            if query.stage not in self.supports_stages:
                return False
        if query.exam_type and self.supports_exam_types:
            if query.exam_type not in self.supports_exam_types:
                return False
        return True


__all__ = [
    "SourceQuery",
    "SourceSample",
    "QualityReport",
    "ExamSourceProvider",
]
