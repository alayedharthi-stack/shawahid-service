"""
conversation_engine.name_intelligence — Arabic name normalisation.

Whisper transliterates Arabic names with predictable drift:

    "اياذ"   → "إياد"
    "الحارفي" → "الحارثي"
    "عائد"   → "عايد"

This module fuzzy-matches a candidate name token-by-token against
curated dictionaries (``data/saudi_names.txt`` for given names,
``data/saudi_lastnames.txt`` for tribal/family names), returning a
normalised spelling plus a confidence score.

Pure module. No DB / GPT / network. The dictionaries are loaded once
at import time and cached.
"""
from __future__ import annotations

import os
from functools import lru_cache

from app.conversation_engine.entity_protection import _arabic_similarity
from app.conversation_engine.schemas import NameCandidate
from app.services.intents import normalize


# Path resolution: ``data/`` lives at the project root (one level up
# from ``app/``). Tests can override via ``CE_DATA_DIR`` env var.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_DEFAULT_DATA_DIR = os.path.join(_PROJECT_ROOT, "data")


# Below this confidence the caller MUST ask the teacher to confirm.
NAME_CONFIDENCE_THRESHOLD = 0.85


def _data_dir() -> str:
    return os.environ.get("CE_DATA_DIR", _DEFAULT_DATA_DIR)


@lru_cache(maxsize=2)
def _load_dictionary(filename: str) -> tuple[str, ...]:
    """Load a name dictionary file (returns canonical spellings)."""
    path = os.path.join(_data_dir(), filename)
    if not os.path.isfile(path):
        return ()
    out: list[str] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            out.append(stripped)
    return tuple(out)


def _given_names() -> tuple[str, ...]:
    return _load_dictionary("saudi_names.txt")


def _last_names() -> tuple[str, ...]:
    return _load_dictionary("saudi_lastnames.txt")


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────


def normalize_full_name(raw: str | None) -> NameCandidate:
    """Best-effort Arabic full-name normalisation.

    Splits ``raw`` on whitespace, fuzzy-matches each token against the
    appropriate dictionary, and re-joins. Returns a ``NameCandidate``
    with a per-token-minimum confidence and a ``needs_confirmation``
    flag derived from ``NAME_CONFIDENCE_THRESHOLD``.
    """
    if not raw or not raw.strip():
        return NameCandidate(
            raw=raw or "",
            normalized="",
            confidence=0.0,
            reason="empty",
            needs_confirmation=True,
        )

    tokens = [t for t in raw.strip().split() if t]
    if not tokens:
        return NameCandidate(
            raw=raw,
            normalized="",
            confidence=0.0,
            reason="empty after split",
            needs_confirmation=True,
        )

    given_pool = _given_names()
    last_pool = _last_names()

    normalized_tokens: list[str] = []
    per_token_confidence: list[float] = []
    reasons: list[str] = []

    for idx, token in enumerate(tokens):
        # The last token (in a 2+ token name) is matched against the
        # tribal/family dictionary first; everything else against given
        # names. This mirrors how Saudi names are usually structured.
        is_last_position = (idx == len(tokens) - 1) and len(tokens) >= 2
        primary = last_pool if is_last_position else given_pool
        secondary = given_pool if is_last_position else last_pool

        match, conf, reason = _best_match(token, primary, secondary)
        normalized_tokens.append(match)
        per_token_confidence.append(conf)
        reasons.append(f"{token}→{match}({conf:.2f})")

    normalized = " ".join(normalized_tokens)
    overall_conf = round(min(per_token_confidence), 2) if per_token_confidence else 0.0
    needs_confirmation = overall_conf < NAME_CONFIDENCE_THRESHOLD

    return NameCandidate(
        raw=raw,
        normalized=normalized,
        confidence=overall_conf,
        reason="; ".join(reasons),
        needs_confirmation=needs_confirmation,
    )


# ──────────────────────────────────────────────────────────────────────
# Token-level matcher
# ──────────────────────────────────────────────────────────────────────


def _best_match(
    token: str,
    primary: tuple[str, ...],
    secondary: tuple[str, ...],
) -> tuple[str, float, str]:
    """Return (chosen, confidence, reason) for a single token.

    The function never errors out — if no dictionary entries are close
    enough, the original token is returned with a low confidence so
    the caller can decide to ask for confirmation.
    """
    norm_token = normalize(token)
    if not norm_token:
        return token, 0.0, "empty"

    # ── Exact normalised hit anywhere wins immediately ─────────────────
    for canonical in primary + secondary:
        if normalize(canonical) == norm_token:
            return canonical, 1.0, "exact"

    # ── Phonetic cleanup pass (cheap, deterministic) ───────────────────
    cleaned = _phonetic_cleanup(norm_token)
    for canonical in primary + secondary:
        if normalize(canonical) == cleaned:
            return canonical, 0.95, "phonetic"

    # ── Fuzzy similarity against primary then secondary pool ───────────
    best_canonical = token
    best_score = 0.0
    best_pool = ""
    for pool_name, pool in (("primary", primary), ("secondary", secondary)):
        for canonical in pool:
            score = _arabic_similarity(norm_token, normalize(canonical))
            if score > best_score:
                best_score = score
                best_canonical = canonical
                best_pool = pool_name

    if best_score >= 0.9:
        return best_canonical, round(best_score, 2), f"fuzzy/{best_pool}"
    if best_score >= 0.75:
        # Likely match, but ask for confirmation.
        return best_canonical, round(best_score, 2), f"weak/{best_pool}"

    # ── Nothing close — keep the original spelling ─────────────────────
    return token, 0.4, "no-match"


# ──────────────────────────────────────────────────────────────────────
# Arabic phonetic cleanup (Whisper drift heuristics)
# ──────────────────────────────────────────────────────────────────────

# Common Whisper / typo substitutions that preserve pronunciation.
_PHONETIC_PAIRS: tuple[tuple[str, str], ...] = (
    ("ذ", "د"),  # اياذ → اياد
    ("ث", "س"),  # سعس → سعث (rare; mainly inverse: see below)
    ("ف", "ث"),  # الحارفي → الحارثي
    ("ح", "ه"),  # less common
    ("ظ", "ز"),
    ("ض", "ز"),
    ("ع", "ا"),  # عائد → ائد (then handled below)
    ("ء", ""),   # remove stray hamzas
    ("ؤ", "و"),
    ("ئ", "ي"),
)


def _phonetic_cleanup(norm: str) -> str:
    """Apply lightweight phonetic substitutions in BOTH directions and
    return the candidate that is closest to a dictionary entry.

    Conservative on purpose — we only flip one substitution at a time
    to avoid mangling actually-correct names."""
    candidates = [norm]
    for src, dst in _PHONETIC_PAIRS:
        if src in norm:
            candidates.append(norm.replace(src, dst))
        if dst and dst in norm:
            candidates.append(norm.replace(dst, src))
    # Pick the LAST mutation as the primary cleanup target. If none of
    # them hit the dictionary directly, the fuzzy step will pick the
    # closest entry anyway.
    return candidates[-1] if candidates else norm


__all__ = [
    "NAME_CONFIDENCE_THRESHOLD",
    "normalize_full_name",
]
