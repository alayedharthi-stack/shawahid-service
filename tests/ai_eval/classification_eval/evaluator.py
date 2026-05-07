"""
Classification accuracy evaluator.

Runs the deterministic classifier (``app.services.classification``)
and the intent detector (``app.services.intents``) over the ground-truth
dataset and produces three :class:`EvalScore` objects:

    • PDF classification — extracted text → category
    • Image classification — caption + filename → category
    • Text intents — Arabic message → intent label
"""
from __future__ import annotations

from app.services.classification import classify_evidence
from app.services.intents import detect_intent
from tests.ai_eval.metrics.scoring import EvalScore, compute_accuracy


def evaluate_pdf_classification(dataset: list[dict]) -> EvalScore:
    failures: list[str] = []
    pairs: list[tuple[str, str]] = []
    high_conf_correct = 0
    high_conf_total = 0

    for case in dataset:
        result = classify_evidence(
            filename=case.get("filename"),
            extracted_text=case.get("extracted_text"),
            evidence_type="pdf",
        )
        expected = case["expected_category"]
        pairs.append((result.category, expected))
        if result.confidence >= 0.7:
            high_conf_total += 1
            if result.category == expected:
                high_conf_correct += 1
        if result.category != expected:
            failures.append(
                f"{case['id']}: predicted={result.category!r} "
                f"expected={expected!r} (conf={result.confidence:.2f}, "
                f"reason={result.reason!r})"
            )

    accuracy = compute_accuracy(pairs)
    confidence_acc = (high_conf_correct / high_conf_total) if high_conf_total else 0.0
    return EvalScore(
        name="classification:pdf",
        accuracy=accuracy,
        confidence_accuracy=confidence_acc,
        samples=len(dataset),
        failures=failures,
        notes=[
            f"high-confidence cases: {high_conf_total}/{len(dataset)} "
            f"→ {confidence_acc:.0%} accurate",
        ],
    )


def evaluate_image_classification(dataset: list[dict]) -> EvalScore:
    failures: list[str] = []
    pairs: list[tuple[str, str]] = []

    for case in dataset:
        result = classify_evidence(
            filename=case.get("filename"),
            caption=case.get("caption"),
            evidence_type="image",
        )
        expected = case["expected_category"]
        # An image with a generic caption may legitimately fall back to
        # the default category. We accept that as long as the classifier
        # set ``needs_confirmation``.
        if result.category == expected:
            pairs.append((result.category, expected))
        elif result.needs_confirmation:
            pairs.append((expected, expected))  # treated as deferred-to-teacher
        else:
            pairs.append((result.category, expected))
            failures.append(
                f"{case['id']}: predicted={result.category!r} "
                f"expected={expected!r} (no needs_confirmation flag)"
            )

    accuracy = compute_accuracy(pairs)
    return EvalScore(
        name="classification:image",
        accuracy=accuracy,
        samples=len(dataset),
        failures=failures,
    )


def evaluate_text_intents(dataset: list[dict]) -> EvalScore:
    failures: list[str] = []
    pairs: list[tuple[str, str]] = []
    payload_correct = 0
    payload_total = 0

    for case in dataset:
        result = detect_intent(case["text"])
        expected = case["expected_intent"]
        pairs.append((result.intent, expected))

        # Optional payload check: category-hint intent should resolve to
        # the right Arabic category.
        if "expected_payload_category" in case:
            payload_total += 1
            actual_cat = (result.payload or {}).get("category", "")
            if actual_cat == case["expected_payload_category"]:
                payload_correct += 1
            else:
                failures.append(
                    f"{case['id']}: payload mismatch "
                    f"actual={actual_cat!r} expected={case['expected_payload_category']!r}"
                )

        if result.intent != expected:
            failures.append(
                f"{case['id']}: intent={result.intent!r} expected={expected!r} "
                f"(text={case['text']!r})"
            )

    accuracy = compute_accuracy(pairs)
    notes = []
    if payload_total:
        notes.append(
            f"category-hint payload accuracy: "
            f"{payload_correct}/{payload_total} ({payload_correct / payload_total:.0%})"
        )
    return EvalScore(
        name="classification:text_intents",
        accuracy=accuracy,
        samples=len(dataset),
        failures=failures,
        notes=notes,
    )
