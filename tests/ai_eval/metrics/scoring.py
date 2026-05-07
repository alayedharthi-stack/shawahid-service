"""
Scoring primitives — pure functions, no I/O.

The evaluation suite measures six dimensions:

    accuracy                   — fraction of predictions matching the
                                 expected label
    confidence_accuracy        — when the model is confident, is it right?
    hallucination_rate         — fraction of empty/short inputs that
                                 produced a non-confirming output
    duplicate_detection_accuracy
                               — fraction of duplicates correctly flagged
    teacher_flow_quality       — qualitative score for tone/noise checks
    export_readiness_score     — review_session warnings match expected
                                 ones

A single :class:`EvalScore` is produced per evaluator, then aggregated
into a final :class:`EvalReport` (see ``reporting.py``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


# ──────────────────────────────────────────────────────────────────────────────
# DTO
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EvalScore:
    """Single-evaluator score envelope."""

    name: str
    accuracy: float = 0.0
    confidence_accuracy: float = 0.0
    hallucination_rate: float = 0.0
    duplicate_detection_accuracy: float = 0.0
    teacher_flow_quality: float = 0.0
    export_readiness_score: float = 0.0
    name_preservation: float = 0.0
    samples: int = 0
    failures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class EvalThresholds:
    """Pass/fail thresholds — used by ``run_eval.py`` to decide exit code.

    Defaults are conservative starting points; tighten them as the
    real intelligence improves.
    """

    min_classification_accuracy: float = 0.75
    min_ocr_accuracy: float = 0.75
    min_audio_name_preservation: float = 0.85
    max_hallucination_rate: float = 0.15
    min_teacher_flow_quality: float = 0.80
    min_export_readiness: float = 0.85


DEFAULT_THRESHOLDS = EvalThresholds()


# ──────────────────────────────────────────────────────────────────────────────
# Scoring helpers
# ──────────────────────────────────────────────────────────────────────────────


def compute_accuracy(predictions: Iterable[tuple[str, str]]) -> float:
    """Given an iterable of ``(predicted, expected)`` pairs, return the
    fraction that match. Returns 0.0 when the iterable is empty.
    """
    pairs = list(predictions)
    if not pairs:
        return 0.0
    correct = sum(1 for p, e in pairs if (p or "").strip() == (e or "").strip())
    return correct / len(pairs)


def compute_hallucination_rate(
    decisions: Iterable[tuple[bool, bool]],
) -> float:
    """Given ``(actually_hallucinated, was_empty_input)`` pairs, return
    the fraction of empty inputs that hallucinated.

    A "hallucination" is when the system *did not* mark the result for
    confirmation despite the input being empty/garbage.
    """
    rows = list(decisions)
    empties = [h for h, e in rows if e]
    if not empties:
        return 0.0
    bad = sum(1 for h in empties if h)
    return bad / len(empties)


def compute_name_preservation(
    cases: Iterable[tuple[str, str, bool, bool]],
) -> float:
    """Given ``(expected_name, transcript, expected_should_confirm,
    actual_should_confirm)`` tuples, return the fraction of cases where
    behaviour matches expectations.

    A correct outcome is:
        • expected_should_confirm == actual_should_confirm
    """
    rows = list(cases)
    if not rows:
        return 0.0
    correct = sum(1 for *_x, exp, act in rows if exp == act)
    return correct / len(rows)


def aggregate_scores(scores: list[EvalScore]) -> EvalScore:
    """Combine multiple per-evaluator scores into one summary score.

    All numeric fields are weighted by ``samples`` so larger evaluations
    carry more weight in the final number.
    """
    if not scores:
        return EvalScore(name="aggregate")
    total_samples = sum(s.samples for s in scores) or 1

    def weighted(field_name: str) -> float:
        return sum(getattr(s, field_name) * (s.samples or 0) for s in scores) / total_samples

    return EvalScore(
        name="aggregate",
        accuracy=weighted("accuracy"),
        confidence_accuracy=weighted("confidence_accuracy"),
        hallucination_rate=weighted("hallucination_rate"),
        duplicate_detection_accuracy=weighted("duplicate_detection_accuracy"),
        teacher_flow_quality=weighted("teacher_flow_quality"),
        export_readiness_score=weighted("export_readiness_score"),
        name_preservation=weighted("name_preservation"),
        samples=total_samples,
        failures=[f for s in scores for f in s.failures],
        notes=[n for s in scores for n in s.notes],
    )


def grade_label(value: float) -> str:
    """Translate a 0-1 score to a short Arabic grade label."""
    if value >= 0.95: return "ممتاز"
    if value >= 0.85: return "جيد جداً"
    if value >= 0.75: return "جيد"
    if value >= 0.60: return "مقبول"
    return "ضعيف"
