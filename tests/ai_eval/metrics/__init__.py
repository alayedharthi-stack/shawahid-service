"""Scoring + reporting primitives for the AI Evaluation Suite."""

from tests.ai_eval.metrics.reporting import (
    EvalReport,
    EvalSection,
    build_markdown_report,
    print_report,
)
from tests.ai_eval.metrics.scoring import (
    DEFAULT_THRESHOLDS,
    EvalScore,
    EvalThresholds,
    aggregate_scores,
    compute_accuracy,
    compute_hallucination_rate,
    compute_name_preservation,
    grade_label,
)

__all__ = [
    "EvalScore",
    "EvalThresholds",
    "DEFAULT_THRESHOLDS",
    "compute_accuracy",
    "compute_hallucination_rate",
    "compute_name_preservation",
    "aggregate_scores",
    "grade_label",
    "EvalReport",
    "EvalSection",
    "build_markdown_report",
    "print_report",
]
