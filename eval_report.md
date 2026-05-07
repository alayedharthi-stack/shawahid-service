# Shawahid AI — Evaluation Report

_Generated: 2026-05-07T20:34:54+00:00_

## Summary

- **Status**: PASS ✅
- **Total samples**: 54
- **Aggregate accuracy**: 98.1% (ممتاز)
- **Hallucination rate**: 0.0%
- **Name preservation**: 100.0% (ممتاز)
- **Teacher flow quality**: 100.0% (ممتاز)
- **Export readiness**: 100.0% (ممتاز)

## Per-evaluator results

| Evaluator | Samples | Accuracy | Hallucination | Notes |
|-----------|---------|----------|---------------|-------|
| classification:pdf | 9 | 100% | 0% | 0 failures |
| classification:image | 3 | 67% | 0% | 1 failures |
| classification:text_intents | 6 | 100% | 0% | 0 failures |
| ocr:keyword_recall | 4 | 100% | 0% | 0 failures |
| audio:name_preservation | 7 | 100% | 0% | 0 failures |
| audio:transcript_classification | 7 | 100% | 0% | 0 failures |
| hallucination:empty_inputs | 4 | 100% | 0% | 0 failures |
| teacher_flow | 9 | 100% | 0% | 0 failures |
| export_readiness | 5 | 100% | 0% | 0 failures |

## Failures (per evaluator)

### classification:image — 1 failures

- board_lesson_01: predicted='التخطيط' expected='نشاط صفي' (no needs_confirmation flag)

## Evaluator notes

- high-confidence cases: 8/9 → 100% accurate
- category-hint payload accuracy: 2/2 (100%)
- keyword recall: 12/12 (100%)
- protected names tested: عايد، الحارثي، حسين، نوف، غيداء
- safe outcomes: needs_confirmation=True OR confidence<0.5

