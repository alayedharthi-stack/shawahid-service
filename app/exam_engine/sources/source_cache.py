"""
exam_engine.sources.source_cache — TTL cache for fetched samples.

Phase-11 contract
=================
Every provider asks the cache before hitting its HTTP client. Cache
keys are deterministic (provider name + canonicalised query + content
hash), so two callers asking for the same exam never trigger a second
network round-trip in the TTL window.

Pure module. No network / DB / GPT.
"""
from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass, field
from typing import Iterable

from app.exam_engine.sources.base import SourceQuery, SourceSample


# Default TTL: 1 hour. Long enough to avoid hammering external sites,
# short enough to pick up fresh content within a school day.
_DEFAULT_TTL_SECONDS = 60 * 60


@dataclass
class _CacheEntry:
    samples: tuple[SourceSample, ...]
    metadata: dict
    expires_at: float


@dataclass
class CacheStats:
    """Used by tests / monitoring to verify cache behaviour."""

    hits: int = 0
    misses: int = 0
    size: int = 0
    last_keys: list[str] = field(default_factory=list)


class SourceCache:
    """Thread-safe in-memory cache.

    Swap to Redis later by replacing ``_BACKEND`` — the public API
    (``get`` / ``put`` / ``invalidate``) stays the same.
    """

    def __init__(self, *, default_ttl: int = _DEFAULT_TTL_SECONDS) -> None:
        self.default_ttl = default_ttl
        self._backend: dict[str, _CacheEntry] = {}
        self._lock = threading.RLock()
        self.stats = CacheStats()

    # ── Public API ────────────────────────────────────────────────────

    def get(
        self,
        provider: str,
        query: SourceQuery,
    ) -> tuple[SourceSample, ...] | None:
        """Return cached samples or ``None`` on miss/expired."""
        key = self._build_key(provider, query)
        now = time.time()
        with self._lock:
            entry = self._backend.get(key)
            if entry is None:
                self.stats.misses += 1
                return None
            if entry.expires_at < now:
                # expired
                self._backend.pop(key, None)
                self.stats.misses += 1
                self.stats.size = len(self._backend)
                return None
            self.stats.hits += 1
            self.stats.last_keys.append(key)
            return entry.samples

    def put(
        self,
        provider: str,
        query: SourceQuery,
        samples: Iterable[SourceSample],
        *,
        ttl: int | None = None,
        metadata: dict | None = None,
    ) -> str:
        """Store ``samples`` keyed by (provider, query)."""
        ttl = self.default_ttl if ttl is None else ttl
        key = self._build_key(provider, query)
        with self._lock:
            self._backend[key] = _CacheEntry(
                samples=tuple(samples),
                metadata=dict(metadata or {}),
                expires_at=time.time() + ttl,
            )
            self.stats.size = len(self._backend)
        return key

    def invalidate(self, provider: str, query: SourceQuery) -> bool:
        key = self._build_key(provider, query)
        with self._lock:
            removed = self._backend.pop(key, None) is not None
            if removed:
                self.stats.size = len(self._backend)
            return removed

    def clear(self) -> None:
        with self._lock:
            self._backend.clear()
            self.stats = CacheStats()

    # ── Hashing helpers ───────────────────────────────────────────────

    @staticmethod
    def _build_key(provider: str, query: SourceQuery) -> str:
        """Stable hash of (provider, query) — used as the cache key."""
        canon = "|".join((
            provider,
            query.subject or "",
            query.grade or "",
            query.stage or "",
            query.semester or "",
            query.exam_type or "",
            query.unit or "",
            query.lesson or "",
        ))
        return hashlib.sha1(canon.encode("utf-8"), usedforsecurity=False).hexdigest()


# ──────────────────────────────────────────────────────────────────────
# Module-level singleton (most callers use this)
# ──────────────────────────────────────────────────────────────────────

_GLOBAL_CACHE = SourceCache()


def get_global_cache() -> SourceCache:
    """Return the process-wide cache instance."""
    return _GLOBAL_CACHE


def reset_global_cache() -> None:
    """Wipe the singleton — used by tests."""
    _GLOBAL_CACHE.clear()


# ──────────────────────────────────────────────────────────────────────
# Content hash (used to detect duplicate samples across providers)
# ──────────────────────────────────────────────────────────────────────


def normalized_content_hash(text: str) -> str:
    """Stable hash of *content* (not URL).

    The hash is normalised: case-folded, whitespace-collapsed, common
    Arabic variants unified. Two providers serving the same paper
    will produce the same hash so the cache + dedup layer can drop
    the duplicate.
    """
    if not text:
        return hashlib.sha1(b"", usedforsecurity=False).hexdigest()
    cleaned = " ".join(text.split()).lower()
    # Drop Arabic diacritics + tatweel so visually-identical text hashes
    # the same. We keep this in-module to avoid a circular import on
    # ``app.services.intents``.
    for ch in "\u064B\u064C\u064D\u064E\u064F\u0650\u0651\u0652\u0640":
        cleaned = cleaned.replace(ch, "")
    return hashlib.sha1(cleaned.encode("utf-8"), usedforsecurity=False).hexdigest()


__all__ = [
    "SourceCache",
    "CacheStats",
    "get_global_cache",
    "reset_global_cache",
    "normalized_content_hash",
]
