"""
exam_engine — independent exam-generation service.

Phase-10 contract
=================
This package provides the foundation for Shawahid AI's exam service.
It is COMPLETELY INDEPENDENT from the shawahid (evidence) pipeline:

    • does not import export_engine, media_engine, review_engine,
      or storage_engine
    • does not touch ``app/templates/exports/ministry_v1`` (the
      shawahid template); exam HTML lives at
      ``app/templates/exams/default_v1``
    • does not call ``services.exporter._generate_pdf``; PDFs go
      through ``exam_engine.exam_export.export_exam_pdf``

Public API
----------

    schemas
        ExamProfile, ExamRequest, ExamQuestion, GeneratedExam,
        ValidationIssue, ValidationResult,
        QTYPE_* / EXAM_TYPE_* / SOURCE_* constants

    exam_profile
        build_exam_profile, merge_profile

    exam_template
        DEFAULT_TEMPLATE_NAME, load_environment

    exam_generator
        generate_exam, GenerationFailure

    exam_validator
        validate_exam

    exam_renderer
        render_exam_html

    exam_export
        export_exam_pdf, ExamExportResult

    prompt_builder
        SYSTEM_ROLE, OUTPUT_SCHEMA_HINT, build_exam_prompt

    messages
        build_exam_request_message, build_exam_missing_info_message,
        build_exam_ready_message, build_exam_failure_message

    sources
        list_providers, LocalSamplesProvider, lookup_sample_questions,
        + placeholder providers for madati / kutubi / manhaji
"""
from __future__ import annotations

from app.exam_engine import (
    exam_defaults,
    exam_export,
    exam_flow,
    exam_generator,
    exam_profile,
    exam_renderer,
    exam_slot_parser,
    exam_template,
    exam_validator,
    messages,
    prompt_builder,
    schemas,
    sources,
)
from app.exam_engine.exam_defaults import ExamDefaults, smart_defaults
from app.exam_engine.exam_flow import (
    ExamFlowResult,
    STAGE_FAILED,
    STAGE_MISSING_INFO,
    STAGE_NO_MATCH,
    STAGE_READY,
    handle_exam_request,
)
from app.exam_engine.exam_slot_parser import ExamSlots, parse_exam_slots
from app.exam_engine.exam_export import ExamExportResult, export_exam_pdf
from app.exam_engine.exam_generator import GenerationFailure, generate_exam
from app.exam_engine.exam_profile import build_exam_profile, merge_profile
from app.exam_engine.exam_renderer import render_exam_html
from app.exam_engine.exam_template import DEFAULT_TEMPLATE_NAME, load_environment
from app.exam_engine.exam_validator import validate_exam
from app.exam_engine.messages import (
    build_exam_failure_message,
    build_exam_generation_progress,
    build_exam_missing_info_message,
    build_exam_ready_message,
    build_exam_request_message,
    build_exam_source_selection_message,
)
from app.exam_engine.prompt_builder import (
    OUTPUT_SCHEMA_HINT,
    SYSTEM_ROLE,
    build_exam_prompt,
)
from app.exam_engine.schemas import (
    EXAM_TYPE_FINAL,
    EXAM_TYPE_HOMEWORK,
    EXAM_TYPE_LABELS_AR,
    EXAM_TYPE_MONTHLY,
    EXAM_TYPE_PRACTICAL,
    EXAM_TYPE_QIYAS,
    EXAM_TYPE_QUICK,
    EXAM_TYPES_ALL,
    QTYPE_FILL_BLANK,
    QTYPE_LABELS_AR,
    QTYPE_MATCH,
    QTYPE_MCQ,
    QTYPE_SHORT,
    QTYPE_TRUE_FALSE,
    QTYPES_ALL,
    SOURCE_CURRICULUM,
    SOURCE_MANUAL_TOPIC,
    SOURCE_MODES_ALL,
    SOURCE_SAMPLE_BANK,
    SOURCE_TEACHER_FILE,
    ExamProfile,
    ExamQuestion,
    ExamRequest,
    GeneratedExam,
    ValidationIssue,
    ValidationResult,
)
from app.exam_engine.sources import (
    AntiCopyOptions,
    CandidateQuestion,
    CurriculumDecision,
    DisabledHttpClient,
    HttpClient,
    HttpResponse,
    InMemoryHttpClient,
    KutubiProvider,
    LocalSamplesProvider,
    MadatiProvider,
    ManhajiProvider,
    NormalizedSample,
    PipelineResult,
    ProviderRunReport,
    QualityReport,
    RequestsHttpClient,
    SourceCache,
    SourceQuery,
    SourceSample,
    TransformationLog,
    anti_copy_transform,
    check_sample_quality,
    filter_by_curriculum,
    get_global_cache,
    list_providers,
    lookup_sample_questions,
    normalize_exam_source,
    normalized_content_hash,
    reset_global_cache,
    run_source_pipeline,
)

__all__ = [
    # submodules
    "schemas", "exam_profile", "exam_template", "exam_generator",
    "exam_validator", "exam_renderer", "exam_export",
    "prompt_builder", "messages", "sources",
    "exam_defaults", "exam_flow", "exam_slot_parser",
    # Phase-12 flow
    "ExamDefaults", "smart_defaults",
    "ExamSlots", "parse_exam_slots",
    "ExamFlowResult", "handle_exam_request",
    "STAGE_MISSING_INFO", "STAGE_READY", "STAGE_NO_MATCH", "STAGE_FAILED",
    # schemas
    "ExamProfile", "ExamQuestion", "ExamRequest", "GeneratedExam",
    "ValidationIssue", "ValidationResult",
    "QTYPE_MCQ", "QTYPE_TRUE_FALSE", "QTYPE_FILL_BLANK",
    "QTYPE_SHORT", "QTYPE_MATCH", "QTYPES_ALL", "QTYPE_LABELS_AR",
    "EXAM_TYPE_QUICK", "EXAM_TYPE_MONTHLY", "EXAM_TYPE_FINAL",
    "EXAM_TYPE_PRACTICAL", "EXAM_TYPE_QIYAS", "EXAM_TYPE_HOMEWORK",
    "EXAM_TYPES_ALL", "EXAM_TYPE_LABELS_AR",
    "SOURCE_TEACHER_FILE", "SOURCE_CURRICULUM",
    "SOURCE_SAMPLE_BANK", "SOURCE_MANUAL_TOPIC", "SOURCE_MODES_ALL",
    # functions
    "build_exam_profile", "merge_profile",
    "DEFAULT_TEMPLATE_NAME", "load_environment",
    "generate_exam", "GenerationFailure",
    "validate_exam",
    "render_exam_html",
    "export_exam_pdf", "ExamExportResult",
    "SYSTEM_ROLE", "OUTPUT_SCHEMA_HINT", "build_exam_prompt",
    "build_exam_request_message", "build_exam_missing_info_message",
    "build_exam_ready_message", "build_exam_failure_message",
    "build_exam_source_selection_message", "build_exam_generation_progress",
    # sources
    "LocalSamplesProvider", "MadatiProvider",
    "KutubiProvider", "ManhajiProvider",
    "list_providers", "lookup_sample_questions",
    "SourceQuery", "SourceSample", "QualityReport",
    "HttpClient", "HttpResponse",
    "DisabledHttpClient", "RequestsHttpClient", "InMemoryHttpClient",
    "NormalizedSample", "CandidateQuestion", "normalize_exam_source",
    "check_sample_quality",
    "AntiCopyOptions", "TransformationLog", "anti_copy_transform",
    "CurriculumDecision", "filter_by_curriculum",
    "SourceCache", "get_global_cache", "reset_global_cache",
    "normalized_content_hash",
    "PipelineResult", "ProviderRunReport", "run_source_pipeline",
]
