"""
Run the full Shawahid AI evaluation suite.

Usage::

    python -m tests.ai_eval.run_eval [--strict] [--report path]

Exit codes:
    0  — all thresholds passed
    1  — at least one threshold violated (failure)
    2  — fatal error during evaluation

In ``--strict`` mode we also require zero per-evaluator failures.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Ensure the project root is on sys.path when run as a script.
_HERE = Path(__file__).resolve()
_SERVICE_ROOT = _HERE.parent.parent.parent
if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))

from tests.ai_eval.audio_eval import (
    evaluate_audio_classification,
    evaluate_name_preservation,
)
from tests.ai_eval.classification_eval import (
    evaluate_image_classification,
    evaluate_pdf_classification,
    evaluate_text_intents,
)
from tests.ai_eval.export_readiness_eval import evaluate_export_readiness
from tests.ai_eval.fixtures.fixture_builder import build_all
from tests.ai_eval.hallucination_eval import evaluate_hallucination
from tests.ai_eval.metrics import (
    DEFAULT_THRESHOLDS,
    EvalReport,
    EvalSection,
    build_markdown_report,
    print_report,
)
from tests.ai_eval.ocr_eval import evaluate_ocr_quality
from tests.ai_eval.teacher_flow_eval import evaluate_teacher_flow
from tests.ai_eval.teacher_flow_eval.evaluator import adapt_dataset

logger = logging.getLogger(__name__)


_DATASET_PATH = Path(__file__).resolve().parent / "datasets" / "expected_results.json"


def load_dataset() -> dict:
    return json.loads(_DATASET_PATH.read_text(encoding="utf-8"))


def run() -> EvalReport:
    """Execute every evaluator and return the populated :class:`EvalReport`."""
    # Make sure binary fixtures exist (idempotent).
    counts = build_all()
    logger.info("fixture_builder produced: %s", counts)

    data = load_dataset()
    report = EvalReport(thresholds=DEFAULT_THRESHOLDS)

    report.add(EvalSection(
        name="classification:pdf",
        score=evaluate_pdf_classification(data["classification_pdf"]),
    ))
    report.add(EvalSection(
        name="classification:image",
        score=evaluate_image_classification(data["classification_image"]),
    ))
    report.add(EvalSection(
        name="classification:text_intents",
        score=evaluate_text_intents(data["classification_text"]),
    ))
    report.add(EvalSection(
        name="ocr:keyword_recall",
        score=evaluate_ocr_quality(data["ocr"]),
    ))
    report.add(EvalSection(
        name="audio:name_preservation",
        score=evaluate_name_preservation(data["audio_transcripts"]),
    ))
    report.add(EvalSection(
        name="audio:transcript_classification",
        score=evaluate_audio_classification(data["audio_transcripts"]),
    ))
    report.add(EvalSection(
        name="hallucination:empty_inputs",
        score=evaluate_hallucination(data["hallucination"]),
    ))
    report.add(EvalSection(
        name="teacher_flow",
        score=evaluate_teacher_flow(adapt_dataset(data["teacher_flow"])),
    ))
    report.add(EvalSection(
        name="export_readiness",
        score=evaluate_export_readiness(data["export_readiness"]),
    ))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Shawahid AI evaluation runner")
    parser.add_argument("--strict", action="store_true",
                        help="require zero per-evaluator failures, not just thresholds")
    parser.add_argument("--report", type=Path, default=None,
                        help="write the markdown report to this path")
    parser.add_argument("--quiet", action="store_true", help="suppress stdout")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        report = run()
    except Exception as exc:
        logger.exception("Evaluation crashed: %s", exc)
        return 2

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(build_markdown_report(report), encoding="utf-8")

    if not args.quiet:
        print_report(report)

    ok, violations = report.passes_thresholds()
    if args.strict:
        n_fails = sum(len(s.score.failures) for s in report.sections)
        if n_fails:
            ok = False
            violations.append(f"strict mode: {n_fails} per-evaluator failures")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
