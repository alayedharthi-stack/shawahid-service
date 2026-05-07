"""
curriculum_engine.learning_outcomes — extract learning outcomes blocks.

Detects the "نواتج التعلم" / "أهداف الدرس" section of a planning
document and pulls out individual outcome lines. Each outcome is
classified by Bloom-taxonomy verb when possible.

Foundation only — phase-9 promise. We don't *evaluate* outcomes here,
just identify and structure them so later phases (curriculum_engine
deeper layers, exam_engine) can build on top.

Pure module. No DB / GPT / network.
"""
from __future__ import annotations

import re

from app.curriculum_engine.schemas import (
    LearningOutcome,
    LearningOutcomesBlock,
)
from app.services.intents import normalize


# ──────────────────────────────────────────────────────────────────────
# Bloom verb dictionary (Arabic)
# ──────────────────────────────────────────────────────────────────────

_BLOOM_VERBS: dict[str, str] = {
    # knowledge / تذكر
    "يذكر": "knowledge", "يعرف": "knowledge", "يسمي": "knowledge",
    "يحدد": "knowledge", "يعدد": "knowledge", "يصف": "knowledge",
    # comprehension / فهم
    "يشرح": "comprehension", "يلخص": "comprehension", "يفسر": "comprehension",
    "يميز": "comprehension", "يقارن": "comprehension",
    # application / تطبيق
    "يطبق": "application", "يحل": "application", "يستخدم": "application",
    "يحسب": "application", "ينفذ": "application",
    # analysis / تحليل
    "يحلل": "analysis", "يستنتج": "analysis", "يربط": "analysis",
    # synthesis / تركيب
    "يبتكر": "synthesis", "يصمم": "synthesis", "يؤلف": "synthesis",
    "ينتج": "synthesis",
    # evaluation / تقويم
    "يقيم": "evaluation", "يحكم": "evaluation", "ينقد": "evaluation",
    "يبرر": "evaluation",
}


# ──────────────────────────────────────────────────────────────────────
# Block detection
# ──────────────────────────────────────────────────────────────────────

# A block is the slice of text that follows a heading like
# "نواتج التعلم" / "الأهداف" until the next obvious heading.
_BLOCK_HEADINGS = (
    "نواتج التعلم",
    "نواتج تعلم",
    "نتائج التعلم",
    "اهداف الدرس",
    "الاهداف",
    "اهداف الوحده",
)

# Line markers we treat as outcome bullets.
_BULLET_RE = re.compile(
    r"(?:^|\n)\s*(?:[\-•◦▪–—\*]|\d+[\.\)]|[أ-ي]\s*[\.\)])\s*(.+?)(?=\n|$)",
    re.MULTILINE,
)


def extract_learning_outcomes(text: str | None) -> LearningOutcomesBlock:
    """Detect outcomes block and split into individual outcomes."""
    if not text:
        return LearningOutcomesBlock()

    norm_full = normalize(text)
    if not norm_full:
        return LearningOutcomesBlock()

    block_text = _slice_block(text, norm_full)
    if not block_text:
        return LearningOutcomesBlock(reason="no outcomes heading")

    raw_lines: list[str] = []

    # Strategy 1: explicit bullets / numbering inside the block.
    for m in _BULLET_RE.finditer(block_text):
        raw_lines.append(m.group(1).strip())

    # Strategy 2: line-by-line fallback (one outcome per line).
    if not raw_lines:
        for line in block_text.splitlines():
            line = line.strip(" \t-•:،")
            if 5 <= len(line) <= 200:
                raw_lines.append(line)

    if not raw_lines:
        return LearningOutcomesBlock(reason="block found, no items")

    outcomes = tuple(_build_outcome(line) for line in raw_lines if line)
    confidence = 0.85 if len(outcomes) >= 2 else 0.6
    return LearningOutcomesBlock(
        outcomes=outcomes,
        confidence=confidence,
        reason=f"{len(outcomes)} outcomes",
    )


def _slice_block(original: str, norm_full: str) -> str | None:
    """Return the substring of ``original`` that follows the outcomes
    heading and stops at the next plausible section heading."""
    lower_norm = norm_full
    start_idx = -1
    for heading in _BLOCK_HEADINGS:
        idx = lower_norm.find(heading)
        if idx != -1:
            start_idx = idx + len(heading)
            break
    if start_idx == -1:
        return None

    # Map normalised offsets back to original text. Normalised text has
    # the same length as the original for our normaliser (only character
    # substitutions, no insertions/deletions). If lengths drift the slice
    # still works approximately and downstream code is tolerant.
    if len(original) >= len(norm_full):
        original_start = start_idx
    else:
        original_start = min(start_idx, len(original))

    tail = original[original_start:original_start + 1500]
    # Cut at next heading.
    for next_heading in (
        "التهيئه", "التهيئة", "العرض", "الاجراءات",
        "التقويم", "الواجب", "استراتيجيات",
        "وسائل التعلم", "المصادر", "الفصل الدراسي",
    ):
        cut = normalize(tail).find(next_heading)
        if cut > 0:
            tail = tail[:cut]
            break
    return tail.strip()


def _build_outcome(line: str) -> LearningOutcome:
    norm_line = normalize(line)
    verb: str | None = None
    bloom: str | None = None

    # Match longer verbs first so "يحلل" wins over the shorter "يحل".
    for v, level in sorted(_BLOOM_VERBS.items(), key=lambda kv: -len(kv[0])):
        if norm_line.startswith(v) or f" {v} " in norm_line or f"ان {v}" in norm_line:
            verb = v
            bloom = level
            break

    return LearningOutcome(raw=line, bloom_level=bloom, verb=verb)


__all__ = ["extract_learning_outcomes"]
