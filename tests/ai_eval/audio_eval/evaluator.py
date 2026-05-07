"""
Audio evaluator — measures the post-transcription pipeline.

We deliberately bypass Whisper itself: production audio → text is GPT-
gated, expensive, and non-deterministic. Instead, this evaluator feeds
hand-curated Arabic transcripts into the deterministic name-detection
heuristic (``intents.looks_like_name_change``) and the classifier.

It answers two questions:

    1. **Name preservation** — does the system flag transcripts containing
       names (عايد، الحارثي، حسين، نوف، غيداء) for confirmation when
       they differ from the saved teacher name?
    2. **Audio classification** — does the classifier route audio
       transcripts to a sane category instead of hallucinating one?
"""
from __future__ import annotations

from app.services.classification import classify_evidence
from app.services.intents import looks_like_name_change
from tests.ai_eval.metrics.scoring import EvalScore, compute_name_preservation


def evaluate_name_preservation(dataset: list[dict]) -> EvalScore:
    failures: list[str] = []
    rows: list[tuple[str, str, bool, bool]] = []

    for case in dataset:
        expected_confirm = bool(case["expected_should_confirm"])
        actual_confirm = looks_like_name_change(
            case["transcript"],
            current_name=case["current_name"],
        )
        rows.append(("", case["transcript"], expected_confirm, actual_confirm))
        if expected_confirm != actual_confirm:
            failures.append(
                f"{case['id']}: expected_confirm={expected_confirm} "
                f"actual={actual_confirm} "
                f"(current={case['current_name']!r}, transcript={case['transcript']!r})"
            )

    name_preservation = compute_name_preservation(rows)
    return EvalScore(
        name="audio:name_preservation",
        accuracy=name_preservation,
        name_preservation=name_preservation,
        samples=len(dataset),
        failures=failures,
        notes=[
            f"protected names tested: عايد، الحارثي، حسين، نوف، غيداء",
        ],
    )


def evaluate_audio_classification(dataset: list[dict]) -> EvalScore:
    """For each transcript, ensure the classifier produces a known
    category (no hallucinated category names).
    """
    from app.services.classification import CATEGORIES

    failures: list[str] = []
    correct = 0
    for case in dataset:
        result = classify_evidence(
            extracted_text=case["transcript"],
            evidence_type="audio",
        )
        if result.category in CATEGORIES:
            correct += 1
        else:
            failures.append(
                f"{case['id']}: classifier returned unknown category "
                f"{result.category!r} (transcript={case['transcript']!r})"
            )

    accuracy = correct / len(dataset) if dataset else 0.0
    return EvalScore(
        name="audio:transcript_classification",
        accuracy=accuracy,
        samples=len(dataset),
        failures=failures,
    )
