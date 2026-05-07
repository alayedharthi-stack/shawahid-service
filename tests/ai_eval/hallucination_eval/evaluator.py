"""
Hallucination evaluator.

Definition of hallucination in this codebase:

    The classifier was given an empty / single-character / noise-only
    input AND chose a *specific* category with high confidence (and
    without flagging ``needs_confirmation``).

A safe outcome on garbage input is one of:
    • ``ClassificationResult.needs_confirmation == True``, OR
    • ``confidence < 0.5``, OR
    • ``category == DEFAULT_CATEGORY`` ("ملفات إدارية") with
      ``needs_confirmation == True``.

Any other behaviour counts as hallucination.
"""
from __future__ import annotations

from app.services.classification import classify_evidence
from tests.ai_eval.metrics.scoring import EvalScore, compute_hallucination_rate


def _is_hallucination(result, expected_needs_confirmation: bool) -> bool:
    if expected_needs_confirmation:
        if result.needs_confirmation:
            return False
        if result.confidence < 0.5:
            return False
    return True


def evaluate_hallucination(dataset: list[dict]) -> EvalScore:
    failures: list[str] = []
    rows: list[tuple[bool, bool]] = []

    for case in dataset:
        result = classify_evidence(
            filename=case.get("filename"),
            extracted_text=case.get("input_text"),
            evidence_type="text",
        )
        expected_needs = bool(case["expected_needs_confirmation"])
        was_garbage = expected_needs
        hallucinated = _is_hallucination(result, expected_needs)
        rows.append((hallucinated, was_garbage))
        if hallucinated:
            failures.append(
                f"{case['id']}: hallucinated category={result.category!r} "
                f"conf={result.confidence:.2f} "
                f"needs_confirmation={result.needs_confirmation} "
                f"(input={case.get('input_text')!r})"
            )

    rate = compute_hallucination_rate(rows)
    correct = sum(1 for h, _ in rows if not h)
    return EvalScore(
        name="hallucination:empty_inputs",
        accuracy=correct / len(rows) if rows else 0.0,
        hallucination_rate=rate,
        samples=len(dataset),
        failures=failures,
        notes=[
            "safe outcomes: needs_confirmation=True OR confidence<0.5",
        ],
    )
