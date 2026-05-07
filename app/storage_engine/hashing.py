"""
storage_engine.hashing — single source of truth for content hashing.

Three hash families, all SHA-256:

    • compute_content_hash(bytes)  → for media (image, video, audio, pdf, doc)
    • hash_text(str)              → for text-only evidences (after NFKD clean)
    • hash_url(str)               → for URL evidences (scheme/host/path only)

These mirror the historical helpers in ``services.deduplication`` so the
DB ``content_hash`` column stays consistent across the migration.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from urllib.parse import urlparse, urlunparse


# ──────────────────────────────────────────────────────────────────────────────
# Content (raw bytes) hash
# ──────────────────────────────────────────────────────────────────────────────


def compute_content_hash(data: bytes) -> str:
    """SHA-256 hex digest of raw file bytes.

    The same byte sequence MUST always hash to the same value across
    runs — this is what enables the dedup layer.
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError(
            f"compute_content_hash expects bytes-like, got {type(data).__name__}"
        )
    return hashlib.sha256(bytes(data)).hexdigest()


# Backwards-compatible alias used by the legacy ``deduplication.hash_bytes``.
hash_bytes = compute_content_hash


# ──────────────────────────────────────────────────────────────────────────────
# Text hash (Arabic-aware)
# ──────────────────────────────────────────────────────────────────────────────


def _clean_text(text: str) -> str:
    """Aggressive Arabic-aware normalisation used for text hashing.

    Identical to the historical ``deduplication._clean`` so existing
    rows keep matching.
    """
    text = unicodedata.normalize("NFKD", text)
    text = re.sub(r"[\u064B-\u065F\u0670]", "", text)  # diacritics
    text = re.sub(r"[أإآٱ]", "ا", text)                # hamza variants
    text = re.sub(r"ة", "ه", text)                     # taa marbuta
    text = re.sub(r"\s+", " ", text).strip()
    return text


def hash_text(text: str) -> str:
    """SHA-256 of normalised Arabic text — used for text-only evidences."""
    if not isinstance(text, str):
        raise TypeError(f"hash_text expects str, got {type(text).__name__}")
    return hashlib.sha256(_clean_text(text).encode("utf-8")).hexdigest()


# ──────────────────────────────────────────────────────────────────────────────
# URL hash
# ──────────────────────────────────────────────────────────────────────────────


def hash_url(url: str) -> str:
    """SHA-256 of normalised URL.

    Strips query/fragment so ``https://youtu.be/abc`` and
    ``https://youtu.be/abc?t=10`` collide.
    """
    if not isinstance(url, str):
        raise TypeError(f"hash_url expects str, got {type(url).__name__}")
    url = url.strip()
    try:
        p = urlparse(url)
        normalised = urlunparse((
            p.scheme.lower(),
            p.netloc.lower(),
            p.path.rstrip("/"),
            "", "", "",
        ))
        return hashlib.sha256(normalised.encode("utf-8")).hexdigest()
    except Exception:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()
