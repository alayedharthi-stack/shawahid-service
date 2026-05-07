"""
Export readiness evaluator.

For each scenario in the dataset we synthesise a list of "evidences"
(``SimpleNamespace`` with the same shape ``review_engine`` expects) and
verify that ``build_review_session`` reports the expected duplicate
and low-confidence counts.

This is the closest deterministic test we can run without a live
database, and it directly measures the signals the WhatsApp webhook
relies on for the pre-export warning.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app.review_engine.review_service import build_review_session
from tests.ai_eval.metrics.scoring import EvalScore


def _make_evidence(spec: dict) -> SimpleNamespace:
    return SimpleNamespace(
        id=int(spec["id"]),
        evidence_type=spec.get("evidence_type", "image"),
        category=spec.get("category"),
        title=spec.get("title", f"شاهد {spec['id']}"),
        content_hash=spec.get("content_hash", f"hash-{spec['id']}"),
        is_duplicate=False,
        is_excluded_from_export=False,
        media_url=None,
        storage_path=None,
        ai_raw={"confidence_score": float(spec.get("confidence", 0.9))},
    )


def evaluate_export_readiness(dataset: list[dict]) -> EvalScore:
    failures: list[str] = []
    checks_total = 0
    checks_ok = 0

    # Stub media URL resolution so the evaluator never touches the
    # filesystem or media_engine specifics.
    mock_urls = SimpleNamespace(
        preview_url=None, thumbnail_url=None, public_url=None,
    )

    for case in dataset:
        evs = [_make_evidence(s) for s in case["evidences"]]
        with patch(
            "app.media_engine.media_urls.build_media_urls",
            return_value=mock_urls,
        ):
            session = build_review_session(
                evs,
                teacher_id=1,
                teacher_name="تركي",
                base_url="https://example.com",
            )

        # ── duplicates ────────────────────────────────────────────────
        if "expected_duplicates_count" in case:
            checks_total += 1
            if session.duplicates_count == case["expected_duplicates_count"]:
                checks_ok += 1
            else:
                failures.append(
                    f"{case['id']}: duplicates_count "
                    f"{session.duplicates_count} != {case['expected_duplicates_count']}"
                )
        if "expected_duplicates_count_min" in case:
            checks_total += 1
            if session.duplicates_count >= case["expected_duplicates_count_min"]:
                checks_ok += 1
            else:
                failures.append(
                    f"{case['id']}: duplicates_count "
                    f"{session.duplicates_count} < min "
                    f"{case['expected_duplicates_count_min']}"
                )

        # ── low confidence ────────────────────────────────────────────
        if "expected_low_confidence_count" in case:
            checks_total += 1
            if session.low_confidence_count == case["expected_low_confidence_count"]:
                checks_ok += 1
            else:
                failures.append(
                    f"{case['id']}: low_confidence_count "
                    f"{session.low_confidence_count} != "
                    f"{case['expected_low_confidence_count']}"
                )
        if "expected_low_confidence_count_min" in case:
            checks_total += 1
            if session.low_confidence_count >= case["expected_low_confidence_count_min"]:
                checks_ok += 1
            else:
                failures.append(
                    f"{case['id']}: low_confidence_count "
                    f"{session.low_confidence_count} < min "
                    f"{case['expected_low_confidence_count_min']}"
                )

        # ── active count ──────────────────────────────────────────────
        if "expected_active_count" in case:
            checks_total += 1
            if session.active_items == case["expected_active_count"]:
                checks_ok += 1
            else:
                failures.append(
                    f"{case['id']}: active_items "
                    f"{session.active_items} != {case['expected_active_count']}"
                )

    score = checks_ok / checks_total if checks_total else 0.0
    return EvalScore(
        name="export_readiness",
        accuracy=score,
        export_readiness_score=score,
        samples=checks_total,
        failures=failures,
    )
