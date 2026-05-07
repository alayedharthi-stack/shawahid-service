"""
exam_engine.sources — pluggable sample-question providers (phase-11).

Phase-11 ships:
    • a real fetch pipeline shared by external providers
    • a TTL-aware ``SourceCache``
    • a ``normalize_exam_source`` step
    • a ``check_sample_quality`` gate
    • an ``anti_copy_transform`` adapter
    • a curriculum-aware filter built on ``curriculum_engine``

External network access stays *off by default*: every provider's
default ``HttpClient`` is ``DisabledHttpClient``. Operators flip a
real client in once the legal/quality review lands.

Public API
----------

    SourceQuery, SourceSample, QualityReport, ExamSourceProvider
    HttpClient, HttpResponse, DisabledHttpClient, RequestsHttpClient,
    InMemoryHttpClient

    NormalizedSample, CandidateQuestion, normalize_exam_source
    QualityFlags, check_sample_quality
    AntiCopyOptions, TransformationLog, anti_copy_transform
    CurriculumDecision, filter_by_curriculum
    SourceCache, get_global_cache, reset_global_cache,
    normalized_content_hash

    PipelineResult, ProviderRunReport, run_source_pipeline

    LocalSamplesProvider, lookup_sample_questions,
    MadatiProvider, KutubiProvider, ManhajiProvider, list_providers
"""
from __future__ import annotations

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
from app.exam_engine.sources.http_client import (
    DisabledHttpClient,
    HttpClient,
    HttpResponse,
    InMemoryHttpClient,
    RequestsHttpClient,
    SourceUnavailable,
    default_client,
)
from app.exam_engine.sources.kutubi import KutubiProvider
from app.exam_engine.sources.local_samples import (
    LocalSamplesProvider,
    lookup_sample_questions,
)
from app.exam_engine.sources.madati import MadatiProvider
from app.exam_engine.sources.manhaji import ManhajiProvider
from app.exam_engine.sources.pipeline import (
    PipelineResult,
    ProviderRunReport,
    run_source_pipeline,
)
from app.exam_engine.sources.source_cache import (
    CacheStats,
    SourceCache,
    get_global_cache,
    normalized_content_hash,
    reset_global_cache,
)
from app.exam_engine.sources.source_normalizer import (
    CandidateQuestion,
    NormalizedSample,
    normalize_exam_source,
)
from app.exam_engine.sources.source_quality import (
    MAX_QUESTIONS_PER_SAMPLE,
    MIN_QUESTIONS_PER_SAMPLE,
    QualityFlags,
    check_sample_quality,
)


def list_providers(
    *,
    only_active: bool = True,
    http_client: HttpClient | None = None,
) -> tuple[ExamSourceProvider, ...]:
    """Return the providers the engine should try, ordered by priority.

    ``only_active`` (the default) keeps ``LocalSamplesProvider`` first
    and includes the external providers only when ``http_client`` is
    supplied OR the global default is no longer ``DisabledHttpClient``.

    Set to ``False`` to enumerate every registered provider regardless
    of whether they're reachable (used by tests / introspection).
    """
    local = LocalSamplesProvider()
    external = (
        MadatiProvider(http_client=http_client),
        KutubiProvider(http_client=http_client),
        ManhajiProvider(http_client=http_client),
    )

    if not only_active:
        return (local, *external)

    # "Active" == has a usable HttpClient injected, OR the caller has
    # opted in via env. We never auto-include external providers when
    # the default disabled client is in effect.
    has_real_client = http_client is not None and not isinstance(
        http_client, DisabledHttpClient,
    )
    if has_real_client:
        return (local, *external)
    return (local,)


__all__ = [
    # base
    "SourceQuery", "SourceSample", "QualityReport", "ExamSourceProvider",
    # http_client
    "HttpClient", "HttpResponse",
    "DisabledHttpClient", "RequestsHttpClient", "InMemoryHttpClient",
    "SourceUnavailable", "default_client",
    # normalizer
    "NormalizedSample", "CandidateQuestion", "normalize_exam_source",
    # quality
    "QualityFlags", "check_sample_quality",
    "MAX_QUESTIONS_PER_SAMPLE", "MIN_QUESTIONS_PER_SAMPLE",
    # anti-copy
    "AntiCopyOptions", "TransformationLog", "anti_copy_transform",
    # curriculum filter
    "CurriculumDecision", "filter_by_curriculum",
    # cache
    "SourceCache", "CacheStats",
    "get_global_cache", "reset_global_cache",
    "normalized_content_hash",
    # pipeline
    "PipelineResult", "ProviderRunReport", "run_source_pipeline",
    # providers
    "LocalSamplesProvider", "lookup_sample_questions",
    "MadatiProvider", "KutubiProvider", "ManhajiProvider",
    "list_providers",
]
