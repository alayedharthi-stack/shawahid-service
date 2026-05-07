"""
Smoke-test wrapper that wires the AI Evaluation Suite into pytest.

We do NOT assert on the threshold pass/fail here — the suite is a
quality measurement tool, not a regression test. The smoke test only
verifies:

    • Every evaluator runs without raising.
    • Every evaluator produces an :class:`EvalScore` with non-zero
      ``samples`` (i.e. the dataset really fed it inputs).
    • The fixture builder is idempotent.

The threshold gate lives in ``tests/ai_eval/run_eval.py`` and is the
right place to fail CI when intelligence regresses.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.ai_eval.fixtures.fixture_builder import build_all
from tests.ai_eval.run_eval import load_dataset, run


def test_dataset_file_loads():
    data = load_dataset()
    for section in (
        "classification_pdf", "classification_image", "classification_text",
        "ocr", "audio_transcripts", "hallucination",
        "teacher_flow", "export_readiness",
    ):
        assert section in data, f"missing dataset section: {section}"
        assert len(data[section]) > 0, f"empty dataset section: {section}"


def test_fixture_builder_is_idempotent(tmp_path):
    """Two consecutive runs must not raise; the second should create
    zero new artifacts.
    """
    first = build_all()
    second = build_all()
    # All counts in the second pass should be 0 (everything already on disk).
    assert all(v == 0 for v in second.values()), (
        f"fixture_builder is not idempotent: {first} → {second}"
    )


def test_real_fixtures_present():
    """Sanity-check that the binary fixtures the user demanded actually
    exist on disk.
    """
    fixtures_root = Path(__file__).resolve().parent / "ai_eval" / "fixtures"
    images = list((fixtures_root / "images").glob("*.jpg"))
    audio  = list((fixtures_root / "audio").glob("*.wav"))
    pdfs   = list((fixtures_root / "pdfs").glob("*.pdf"))
    text   = list((fixtures_root / "text").glob("*.json"))
    assert len(images) >= 3, f"expected real JPEG fixtures, got {images}"
    assert len(audio)  >= 2, f"expected real WAV fixtures, got {audio}"
    assert len(pdfs)   >= 5, f"expected real PDF fixtures, got {pdfs}"
    assert len(text)   >= 1, f"expected real text fixtures, got {text}"


def test_run_eval_returns_report_with_all_sections():
    report = run()
    names = {s.name for s in report.sections}
    expected = {
        "classification:pdf",
        "classification:image",
        "classification:text_intents",
        "ocr:keyword_recall",
        "audio:name_preservation",
        "audio:transcript_classification",
        "hallucination:empty_inputs",
        "teacher_flow",
        "export_readiness",
    }
    assert expected.issubset(names), (
        f"missing evaluators: {expected - names}"
    )


def test_every_evaluator_received_samples():
    report = run()
    for s in report.sections:
        assert s.score.samples > 0, (
            f"evaluator {s.name!r} reported zero samples — dataset wiring is broken"
        )


def test_aggregate_score_is_within_unit_interval():
    report = run()
    agg = report.aggregate()
    for field in ("accuracy", "hallucination_rate", "name_preservation",
                  "teacher_flow_quality", "export_readiness_score"):
        v = getattr(agg, field)
        assert 0.0 <= v <= 1.0, f"{field} out of range: {v}"


def test_markdown_report_renders():
    from tests.ai_eval.metrics import build_markdown_report
    report = run()
    md = build_markdown_report(report)
    assert "# Shawahid AI" in md
    assert "Aggregate accuracy" in md
    assert "Per-evaluator results" in md
