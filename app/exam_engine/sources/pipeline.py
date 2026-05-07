"""
exam_engine.sources.pipeline — orchestrates the full source flow.

The phase-11 brief specifies this exact order:

    fetch
    → normalize
    → extract questions
    → quality check
    → deduplicate
    → adapt (anti-copy)
    → generate final exam

This module wires those steps together and returns a single
``PipelineResult`` so callers don't have to know about each layer.

Pure module aside from delegating to providers (which themselves rely
on the pluggable HTTP client). Catches every provider exception so a
single misbehaving source never takes down the engine.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.exam_engine.schemas import ExamQuestion
from app.exam_engine.sources.anti_copy import (
    AntiCopyOptions,
    TransformationLog,
    anti_copy_transform,
)
from app.exam_engine.sources.base import (
    ExamSourceProvider,
    QualityReport,
    SourceQuery,
    SourceSample,
)
from app.exam_engine.sources.curriculum_filter import (
    CurriculumDecision,
    filter_by_curriculum,
)
from app.exam_engine.sources.source_cache import (
    SourceCache,
    get_global_cache,
    normalized_content_hash,
)
from app.exam_engine.sources.source_normalizer import (
    NormalizedSample,
    normalize_exam_source,
)
from app.exam_engine.sources.source_quality import check_sample_quality

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Result DTOs
# ──────────────────────────────────────────────────────────────────────


@dataclass
class ProviderRunReport:
    """Detailed log for one provider's run inside the pipeline."""

    provider: str
    fetched: int = 0
    accepted: int = 0
    skipped: int = 0
    failure: str | None = None
    flags: list[str] = field(default_factory=list)
    samples: list[NormalizedSample] = field(default_factory=list)


@dataclass
class PipelineResult:
    """Final output of ``run_source_pipeline``."""

    questions: tuple[ExamQuestion, ...]
    transformation: TransformationLog | None
    reports: tuple[ProviderRunReport, ...]
    accepted_samples: int
    skipped_samples: int

    @property
    def has_questions(self) -> bool:
        return bool(self.questions)


# ──────────────────────────────────────────────────────────────────────
# Pipeline entry-point
# ──────────────────────────────────────────────────────────────────────


def run_source_pipeline(
    providers: tuple[ExamSourceProvider, ...],
    *,
    query: SourceQuery,
    anti_copy: AntiCopyOptions | None = None,
    cache: SourceCache | None = None,
    max_questions: int = 30,
) -> PipelineResult:
    """Run every provider through the full pipeline and return the merged
    question set.

    A provider that raises is *isolated* — its report records the
    failure and the pipeline moves on.
    """
    cache = cache or get_global_cache()
    reports: list[ProviderRunReport] = []
    seen_hashes: set[str] = set()
    accepted_questions: list[ExamQuestion] = []

    for provider in providers:
        report = ProviderRunReport(provider=provider.name)
        reports.append(report)

        if not provider.can_handle(query):
            report.failure = "skipped:cannot_handle"
            continue

        # ── 1. fetch (cache aware) ───────────────────────────────────
        try:
            cached = cache.get(provider.name, query)
            if cached is not None:
                samples = cached
                report.flags.append("cache_hit")
            else:
                samples = tuple(provider.fetch(query) or ())
                cache.put(provider.name, query, samples)
        except Exception as exc:  # noqa: BLE001 — provider isolation
            logger.warning(
                "[PIPELINE] provider %s.fetch failed: %s",
                provider.name, exc,
            )
            report.failure = f"fetch_failed:{exc}"
            continue

        report.fetched = len(samples)
        if not samples:
            report.failure = report.failure or "no_samples"
            continue

        for raw in samples:
            try:
                outcome = _process_sample(
                    provider=provider,
                    raw=raw,
                    query=query,
                    seen_hashes=seen_hashes,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[PIPELINE] %s sample failed: %s",
                    provider.name, exc,
                )
                report.skipped += 1
                report.flags.append(f"sample_error:{exc}")
                continue

            if outcome is None:
                report.skipped += 1
                continue

            sample, questions, flags = outcome
            report.samples.append(sample)
            report.flags.extend(flags)
            report.accepted += 1
            accepted_questions.extend(questions)

            if len(accepted_questions) >= max_questions:
                break

        if len(accepted_questions) >= max_questions:
            break

    accepted_questions = accepted_questions[:max_questions]

    # ── 6. anti-copy adapt ────────────────────────────────────────────
    transform_log: TransformationLog | None = None
    if accepted_questions:
        adapted, transform_log = anti_copy_transform(
            tuple(accepted_questions),
            options=anti_copy,
        )
        accepted_questions = list(adapted)

    accepted_total = sum(r.accepted for r in reports)
    skipped_total = sum(r.skipped for r in reports)

    return PipelineResult(
        questions=tuple(accepted_questions),
        transformation=transform_log,
        reports=tuple(reports),
        accepted_samples=accepted_total,
        skipped_samples=skipped_total,
    )


# ──────────────────────────────────────────────────────────────────────
# Single-sample processing
# ──────────────────────────────────────────────────────────────────────


def _process_sample(
    *,
    provider: ExamSourceProvider,
    raw: SourceSample,
    query: SourceQuery,
    seen_hashes: set[str],
):
    """Run normalize → quality → curriculum → questions for one sample.

    Returns ``None`` to indicate the sample was rejected (with reason
    captured in the report flags collected by the caller). On success
    returns ``(NormalizedSample, tuple[ExamQuestion], flags)``.
    """
    flags: list[str] = []

    # ── 2. normalize ──────────────────────────────────────────────────
    normalized = normalize_exam_source(raw)
    if not normalized.questions:
        return None

    # Cross-provider de-dup — a paper found in two sources counts once.
    canonical = "\n".join(q.text for q in normalized.questions)
    canonical_hash = normalized_content_hash(canonical)
    if canonical_hash in seen_hashes:
        flags.append("cross_provider_duplicate")
        return None
    seen_hashes.add(canonical_hash)

    # ── 4. quality check ──────────────────────────────────────────────
    qreport: QualityReport = check_sample_quality(
        normalized,
        expected_subject=query.subject,
        expected_grade=query.grade,
        expected_stage=query.stage,
        expected_semester=query.semester,
    )
    if not qreport.is_acceptable:
        flags.append(f"quality_rejected:{qreport.reason}")
        flags.extend(qreport.flags)
        return None

    # ── 7. curriculum filter ──────────────────────────────────────────
    decision: CurriculumDecision = filter_by_curriculum(normalized, query=query)
    if not decision.is_acceptable:
        flags.append(f"curriculum_rejected:{decision.reason}")
        flags.extend(decision.flags)
        return None

    # ── 3 + 5. extract questions + per-sample dedup ───────────────────
    questions = tuple(provider.extract_questions(raw))
    questions = _dedup_questions(questions)

    return normalized, questions, flags


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _dedup_questions(
    questions: tuple[ExamQuestion, ...],
) -> tuple[ExamQuestion, ...]:
    seen: set[str] = set()
    out: list[ExamQuestion] = []
    for q in questions:
        key = " ".join((q.text or "").split()).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(q)
    return tuple(out)


__all__ = [
    "ProviderRunReport",
    "PipelineResult",
    "run_source_pipeline",
]
