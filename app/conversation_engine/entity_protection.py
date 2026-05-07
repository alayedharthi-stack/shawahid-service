"""
conversation_engine.entity_protection — protect confirmed entities.

Once a teacher has *confirmed* a name (via the ✅ confirmation loop),
no later GPT or Whisper output should silently overwrite it. This
module is the gate every name-write must pass through.

Pure module. No DB / network — the caller passes both the existing
value and the proposed replacement and we return whether the write
should proceed.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.services.intents import normalize


# Anything below this fuzzy-similarity score is treated as a different
# name (not a small typo). 0.0–1.0 scale.
_NAME_SIMILARITY_THRESHOLD = 0.85


@dataclass(frozen=True)
class ProtectionDecision:
    """Outcome of checking a proposed entity overwrite.

    ``allow`` — caller may persist ``proposed``.
    ``needs_confirmation`` — caller should ask the teacher first.
    ``reason`` — short audit string for logs / tests.
    """

    allow: bool
    needs_confirmation: bool
    reason: str


def can_overwrite_name(
    *,
    current: str | None,
    proposed: str | None,
    confirmed: bool = True,
) -> ProtectionDecision:
    """Decide whether ``proposed`` may replace ``current``.

    Rules:
        • No current value → always allow (with confirmation if
          confidence is unknown — caller decides).
        • Current value not yet confirmed → allow.
        • Proposed equals current (ignoring diacritics / hamza /
          spacing) → allow as no-op.
        • Proposed is a *fuzzy variant* (common Whisper drift) →
          allow direct write only if explicitly marked confirmed,
          else stage for confirmation.
        • Proposed is clearly different → never silent overwrite —
          caller must ask the teacher.
    """
    if not proposed or not proposed.strip():
        return ProtectionDecision(False, False, "empty proposed name")

    if not current or not current.strip():
        return ProtectionDecision(True, False, "no existing name")

    if not confirmed:
        return ProtectionDecision(True, False, "current not yet confirmed")

    nc, np = normalize(current), normalize(proposed)
    if nc == np:
        return ProtectionDecision(True, False, "no-op (equal after normalisation)")

    similarity = _arabic_similarity(nc, np)
    if similarity >= _NAME_SIMILARITY_THRESHOLD:
        # Almost the same — likely a typo or Whisper drift. Stage it.
        return ProtectionDecision(
            allow=False,
            needs_confirmation=True,
            reason=f"fuzzy variant (sim={similarity:.2f}) — needs confirmation",
        )

    # Fundamentally different name — block the silent overwrite.
    return ProtectionDecision(
        allow=False,
        needs_confirmation=True,
        reason=f"different name (sim={similarity:.2f}) — needs confirmation",
    )


# ──────────────────────────────────────────────────────────────────────
# String similarity
# ──────────────────────────────────────────────────────────────────────


def _arabic_similarity(a: str, b: str) -> float:
    """Length-normalised Levenshtein similarity, 0-1."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    distance = _levenshtein(a, b)
    return 1.0 - distance / max(len(a), len(b))


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)

    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            curr.append(min(
                curr[j - 1] + 1,        # insert
                prev[j] + 1,            # delete
                prev[j - 1] + cost,     # substitute
            ))
        prev = curr
    return prev[-1]


__all__ = [
    "ProtectionDecision",
    "can_overwrite_name",
]
