"""
Reporting — turns ``EvalScore`` instances into a human-readable report.

The output is plain Markdown / text so the same writer can serve a
terminal printout, a CI artifact, or a future admin endpoint.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import datetime, timezone

from tests.ai_eval.metrics.scoring import (
    DEFAULT_THRESHOLDS,
    EvalScore,
    EvalThresholds,
    aggregate_scores,
    grade_label,
)


@dataclass
class EvalSection:
    """One row in the final report."""
    name: str
    score: EvalScore
    notes: list[str] = field(default_factory=list)


@dataclass
class EvalReport:
    sections: list[EvalSection] = field(default_factory=list)
    thresholds: EvalThresholds = field(default_factory=lambda: DEFAULT_THRESHOLDS)
    generated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def add(self, section: EvalSection) -> None:
        self.sections.append(section)

    def aggregate(self) -> EvalScore:
        """Aggregate every section's score into a summary row.

        For *general* metrics (accuracy, hallucination_rate) we sample-
        weight across all sections.

        For *specialized* metrics (name_preservation, teacher_flow_quality,
        export_readiness_score) we read the value directly from the
        section that owns it — averaging across sections that don't
        measure the metric drags the headline number to zero.
        """
        from tests.ai_eval.metrics.scoring import aggregate_scores

        base = aggregate_scores([s.score for s in self.sections])

        au = self._lookup("name_preservation") or self._lookup("audio")
        flow = self._lookup("teacher_flow")
        exp = self._lookup("export_readiness")

        return EvalScore(
            name=base.name,
            accuracy=base.accuracy,
            confidence_accuracy=base.confidence_accuracy,
            hallucination_rate=base.hallucination_rate,
            duplicate_detection_accuracy=base.duplicate_detection_accuracy,
            teacher_flow_quality=flow.score.teacher_flow_quality if flow else 0.0,
            export_readiness_score=exp.score.export_readiness_score if exp else 0.0,
            name_preservation=au.score.name_preservation if au else 0.0,
            samples=base.samples,
            failures=base.failures,
            notes=base.notes,
        )

    # ── Pass/fail ─────────────────────────────────────────────────────
    def passes_thresholds(self) -> tuple[bool, list[str]]:
        """Return (ok, list_of_violations). Violations are formatted
        Arabic strings ready for display.
        """
        agg = self.aggregate()
        violations: list[str] = []
        t = self.thresholds

        cls = self._lookup("classification")
        if cls and cls.score.accuracy < t.min_classification_accuracy:
            violations.append(
                f"دقة التصنيف منخفضة: {cls.score.accuracy:.0%} "
                f"< الحد الأدنى {t.min_classification_accuracy:.0%}"
            )

        ocr = self._lookup("ocr")
        if ocr and ocr.score.accuracy < t.min_ocr_accuracy:
            violations.append(
                f"دقة OCR منخفضة: {ocr.score.accuracy:.0%} "
                f"< الحد الأدنى {t.min_ocr_accuracy:.0%}"
            )

        au = self._lookup("audio")
        if au and au.score.name_preservation < t.min_audio_name_preservation:
            violations.append(
                f"حماية الأسماء منخفضة: {au.score.name_preservation:.0%} "
                f"< الحد الأدنى {t.min_audio_name_preservation:.0%}"
            )

        if agg.hallucination_rate > t.max_hallucination_rate:
            violations.append(
                f"معدل الهلوسة مرتفع: {agg.hallucination_rate:.0%} "
                f"> الحد الأقصى {t.max_hallucination_rate:.0%}"
            )

        flow = self._lookup("teacher_flow")
        if flow and flow.score.teacher_flow_quality < t.min_teacher_flow_quality:
            violations.append(
                f"جودة تجربة المعلم منخفضة: {flow.score.teacher_flow_quality:.0%} "
                f"< الحد الأدنى {t.min_teacher_flow_quality:.0%}"
            )

        exp = self._lookup("export_readiness")
        if exp and exp.score.export_readiness_score < t.min_export_readiness:
            violations.append(
                f"جاهزية التصدير منخفضة: {exp.score.export_readiness_score:.0%} "
                f"< الحد الأدنى {t.min_export_readiness:.0%}"
            )

        return (len(violations) == 0, violations)

    def _lookup(self, key_substring: str) -> EvalSection | None:
        for s in self.sections:
            if key_substring.lower() in s.name.lower():
                return s
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Renderers
# ──────────────────────────────────────────────────────────────────────────────


def build_markdown_report(report: EvalReport) -> str:
    """Format an :class:`EvalReport` as Markdown."""
    buf = io.StringIO()
    w = buf.write

    w(f"# Shawahid AI — Evaluation Report\n\n")
    w(f"_Generated: {report.generated_at.isoformat(timespec='seconds')}_\n\n")

    agg = report.aggregate()
    ok, violations = report.passes_thresholds()
    status_label = "PASS ✅" if ok else "FAIL ❌"

    w("## Summary\n\n")
    w(f"- **Status**: {status_label}\n")
    w(f"- **Total samples**: {agg.samples}\n")
    w(f"- **Aggregate accuracy**: {agg.accuracy:.1%} ({grade_label(agg.accuracy)})\n")
    w(f"- **Hallucination rate**: {agg.hallucination_rate:.1%}\n")
    w(f"- **Name preservation**: {agg.name_preservation:.1%} "
      f"({grade_label(agg.name_preservation)})\n")
    w(f"- **Teacher flow quality**: {agg.teacher_flow_quality:.1%} "
      f"({grade_label(agg.teacher_flow_quality)})\n")
    w(f"- **Export readiness**: {agg.export_readiness_score:.1%} "
      f"({grade_label(agg.export_readiness_score)})\n\n")

    if violations:
        w("### Threshold violations\n\n")
        for v in violations:
            w(f"- ❌ {v}\n")
        w("\n")

    w("## Per-evaluator results\n\n")
    w("| Evaluator | Samples | Accuracy | Hallucination | Notes |\n")
    w("|-----------|---------|----------|---------------|-------|\n")
    for s in report.sections:
        w(f"| {s.name} | {s.score.samples} | "
          f"{s.score.accuracy:.0%} | "
          f"{s.score.hallucination_rate:.0%} | "
          f"{len(s.score.failures)} failures |\n")
    w("\n")

    failing = [s for s in report.sections if s.score.failures]
    if failing:
        w("## Failures (per evaluator)\n\n")
        for s in failing:
            w(f"### {s.name} — {len(s.score.failures)} failures\n\n")
            for f in s.score.failures[:20]:
                w(f"- {f}\n")
            if len(s.score.failures) > 20:
                w(f"- _… {len(s.score.failures) - 20} more truncated_\n")
            w("\n")

    notes = [n for s in report.sections for n in s.score.notes]
    if notes:
        w("## Evaluator notes\n\n")
        for n in notes:
            w(f"- {n}\n")
        w("\n")

    return buf.getvalue()


def print_report(report: EvalReport) -> None:
    """Print the markdown report to stdout."""
    print(build_markdown_report(report))
