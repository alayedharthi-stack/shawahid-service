"""
AI Evaluation Suite — Phase 8 (Quality & Intelligence Assurance).

This package is *not* a feature for end users. It exists so we can
measure the real intelligence of Shawahid AI before the curriculum
engine work begins.

Sub-packages:
    fixtures/                 — real files used by the evaluators
    classification_eval/      — category-prediction accuracy
    ocr_eval/                 — text extraction quality
    audio_eval/               — name preservation + transcript handling
    hallucination_eval/       — refusal-on-empty-input checks
    teacher_flow_eval/        — message tone & noise checks
    export_readiness_eval/    — importance + dedup + review readiness
    metrics/                  — scoring + reporting primitives
    datasets/                 — ground-truth JSON
    run_eval.py               — orchestrator
"""
