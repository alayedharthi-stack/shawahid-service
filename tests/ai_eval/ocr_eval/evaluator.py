"""
OCR / text-extraction quality evaluator.

We do not run a real OCR engine here — production uses GPT Vision —
so this evaluator answers a different question:

    Given a raw OCR/extracted text snippet (clean, blurry, phone-camera,
    handwritten), does the deterministic classifier still find enough
    Arabic keywords to make a sensible classification?

The metric is "keyword recall after Arabic normalisation". A real OCR
pipeline can be plugged in later (pdfplumber, pytesseract) by replacing
``input_text`` with the engine's actual output.
"""
from __future__ import annotations

from app.services.intents import normalize
from tests.ai_eval.metrics.scoring import EvalScore


def evaluate_ocr_quality(dataset: list[dict]) -> EvalScore:
    failures: list[str] = []
    matched_total = 0
    expected_total = 0

    for case in dataset:
        normalised = normalize(case.get("input_text", ""))
        expected_keywords = case.get("expected_keywords", [])
        min_matches = int(case.get("min_keyword_matches", 1))

        hits = sum(1 for kw in expected_keywords if normalize(kw) in normalised)
        matched_total += hits
        expected_total += len(expected_keywords)

        if hits < min_matches:
            failures.append(
                f"{case['id']}: matched {hits}/{len(expected_keywords)} keywords "
                f"(< {min_matches} threshold)"
            )

    recall = (matched_total / expected_total) if expected_total else 0.0
    return EvalScore(
        name="ocr:keyword_recall",
        accuracy=recall,
        samples=len(dataset),
        failures=failures,
        notes=[
            f"keyword recall: {matched_total}/{expected_total} "
            f"({recall:.0%})",
        ],
    )
