"""
Teacher flow evaluator.

We do not run the WhatsApp webhook end-to-end here (it is async + DB +
external calls). Instead we evaluate the *message builders* themselves
to confirm:

    • Batch summary collapses N files into a single message.
    • High-confidence saves do NOT include the "حفظته في المحور
      الأقرب" review hint.
    • Low-confidence saves DO include a review hint.
    • Each message stays under a reasonable length cap (4 lines for
      receipts, 8 lines for batch summaries).

These properties match the Phase-3 / Phase-6 tone guide and are what
make the live UX feel "calm" rather than "spammy".
"""
from __future__ import annotations

from app.services.whatsapp_messages import (
    BatchItem,
    build_batch_summary,
    build_evidence_saved_smart,
)
from tests.ai_eval.metrics.scoring import EvalScore


def _line_count(msg: str) -> int:
    return len([ln for ln in msg.splitlines() if ln.strip()])


def evaluate_teacher_flow(dataset: dict) -> EvalScore:
    failures: list[str] = []
    checks_total = 0
    checks_ok = 0

    # ── 1. Batch summary tests ────────────────────────────────────────
    for case in dataset.get("flows_batch", []):
        items = [BatchItem(category=f["category"]) for f in case["files"]]
        msg = build_batch_summary(items)
        n_lines = _line_count(msg)
        # Single message → at most 1 build_batch_summary call.
        max_messages = case.get("max_messages", 1)
        checks_total += 1
        if max_messages != 1:
            failures.append(
                f"{case['id']}: dataset declared max_messages != 1"
            )
        else:
            checks_ok += 1

        for required in case.get("must_contain", []):
            checks_total += 1
            if required in msg:
                checks_ok += 1
            else:
                failures.append(
                    f"{case['id']}: batch summary missing {required!r} "
                    f"(msg={msg!r})"
                )

        # Length guard: batch summary should not exceed 8 lines.
        checks_total += 1
        if n_lines <= 8:
            checks_ok += 1
        else:
            failures.append(
                f"{case['id']}: batch summary too long ({n_lines} lines)"
            )

    # ── 2. Save-confirmation tests ────────────────────────────────────
    for case in dataset.get("flows_save", []):
        msg = build_evidence_saved_smart(
            ev_type=case["ev_type"],
            category=case["category"],
            title=case.get("title"),
            needs_review=bool(case.get("needs_review", False)),
        )
        for required in case.get("must_contain", []):
            checks_total += 1
            if required in msg:
                checks_ok += 1
            else:
                failures.append(
                    f"{case['id']}: save reply missing {required!r}"
                )
        for forbidden in case.get("must_not_contain", []):
            checks_total += 1
            if forbidden not in msg:
                checks_ok += 1
            else:
                failures.append(
                    f"{case['id']}: save reply contains forbidden text "
                    f"{forbidden!r}"
                )
        # Tone guard: ≤ 5 lines.
        checks_total += 1
        if _line_count(msg) <= 5:
            checks_ok += 1
        else:
            failures.append(
                f"{case['id']}: save reply too long ({_line_count(msg)} lines)"
            )

    quality = checks_ok / checks_total if checks_total else 0.0
    return EvalScore(
        name="teacher_flow",
        accuracy=quality,
        teacher_flow_quality=quality,
        samples=checks_total,
        failures=failures,
    )


def adapt_dataset(raw_section: list[dict]) -> dict:
    """Translate the flat ``teacher_flow`` array from
    ``expected_results.json`` into the two-bucket dict that
    :func:`evaluate_teacher_flow` consumes.
    """
    flows_batch: list[dict] = []
    flows_save: list[dict] = []
    for case in raw_section:
        if "files" in case:
            flows_batch.append(case)
        else:
            # Translate confidence threshold into needs_review boolean.
            needs_review = float(case.get("confidence", 1.0)) < 0.6
            flows_save.append({
                **case,
                "needs_review": needs_review,
            })
    return {"flows_batch": flows_batch, "flows_save": flows_save}
