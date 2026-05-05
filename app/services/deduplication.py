"""
Deduplication service for Shawahid AI.

Three deduplication layers:
  1. Media / file  — SHA-256 of raw bytes (exact match)
  2. Text message  — difflib ratio on cleaned Arabic text (≥ 90% → duplicate)
  3. URL           — normalized URL hash (exact match, ignores query params)

Used at two checkpoints:
  • Save time   (webhook.py)  — stop before create_evidence()
  • Export time (exporter.py) — filter evidences list before rendering PDF
"""
import difflib
import hashlib
import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, urlunparse

from sqlalchemy.orm import Session

from app.models.evidence import Evidence

logger = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────
TEXT_DUPLICATE_RATIO  = 0.90   # ≥ 90% → skip (exact duplicate)
TEXT_MIN_LENGTH       = 20     # shorter texts are not compared
TEXT_LOOKBACK_DAYS    = 90     # only compare against evidences from last N days
TEXT_LOOKBACK_LIMIT   = 60     # max DB rows to load for comparison


# ── Hash helpers ──────────────────────────────────────────────────────────────

def hash_bytes(data: bytes) -> str:
    """SHA-256 of raw file bytes — for images, videos, audio, documents."""
    return hashlib.sha256(data).hexdigest()


def hash_text(text: str) -> str:
    """SHA-256 of cleaned, normalised Arabic text."""
    return hashlib.sha256(_clean(text).encode("utf-8")).hexdigest()


def hash_url(url: str) -> str:
    """
    SHA-256 of normalised URL.
    Strips query-params and fragment; lowercases scheme + host + path.
    e.g. https://youtu.be/abc?t=10 and https://youtu.be/abc become the same hash.
    """
    try:
        p = urlparse(url.strip())
        normalised = urlunparse((
            p.scheme.lower(),
            p.netloc.lower(),
            p.path.rstrip("/"),
            "", "", "",          # strip params, query, fragment
        ))
        return hashlib.sha256(normalised.encode()).hexdigest()
    except Exception:
        return hashlib.sha256(url.strip().encode()).hexdigest()


# ── Arabic text cleaning ──────────────────────────────────────────────────────

def _clean(text: str) -> str:
    """Normalise Arabic text for similarity comparison."""
    # Unicode normalisation
    text = unicodedata.normalize("NFKD", text)
    # Remove Arabic diacritics (harakat / tashkeel)
    text = re.sub(r"[\u064B-\u065F\u0670]", "", text)
    # Unify alef variants (أإآٱ → ا)
    text = re.sub(r"[أإآٱ]", "ا", text)
    # Unify teh marbuta (ة → ه)
    text = re.sub(r"ة", "ه", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ── DB-level duplicate checks ─────────────────────────────────────────────────

def is_exact_duplicate(db: Session, teacher_id: int, content_hash: str) -> bool:
    """
    Returns True if an evidence with the same content_hash already exists
    for this teacher. Works for media, text, and URL hashes.
    """
    exists = (
        db.query(Evidence.id)
        .filter(
            Evidence.teacher_id   == teacher_id,
            Evidence.content_hash == content_hash,
        )
        .first()
    )
    return exists is not None


def find_near_duplicate_text(
    db: Session,
    teacher_id: int,
    text: str,
) -> Evidence | None:
    """
    Compare new text against the teacher's recent text evidences.
    Returns the existing Evidence if similarity ≥ TEXT_DUPLICATE_RATIO, else None.

    Only called for pure-text messages (no media).
    """
    if len(text.strip()) < TEXT_MIN_LENGTH:
        return None   # Too short to compare meaningfully

    cutoff = datetime.now(timezone.utc) - timedelta(days=TEXT_LOOKBACK_DAYS)
    recent: list[Evidence] = (
        db.query(Evidence)
        .filter(
            Evidence.teacher_id    == teacher_id,
            Evidence.evidence_type == "text",
            Evidence.created_at    >= cutoff,
        )
        .order_by(Evidence.created_at.desc())
        .limit(TEXT_LOOKBACK_LIMIT)
        .all()
    )

    clean_new = _clean(text)
    for ev in recent:
        existing = ev.message_text or ""
        if not existing:
            continue
        ratio = difflib.SequenceMatcher(None, clean_new, _clean(existing)).ratio()
        if ratio >= TEXT_DUPLICATE_RATIO:
            logger.info(
                "[DEDUP] near-duplicate text ratio=%.2f teacher_id=%d existing_id=%d",
                ratio, teacher_id, ev.id,
            )
            return ev

    return None


# ── Export-time deduplication ──────────────────────────────────────────────────

def _best_of(a: dict, b: dict) -> dict:
    """
    Given two normalised evidence dicts, return the one with richer metadata.
    Preference: longer description > longer title > later created_at.
    """
    score_a = len(a.get("description") or "") + len(a.get("title") or "") * 0.5
    score_b = len(b.get("description") or "") + len(b.get("title") or "") * 0.5
    return a if score_a >= score_b else b


def _title_key(title: str) -> str:
    """Normalised title for near-duplicate title detection."""
    return _clean(title).lower()


def deduplicate_for_export(evidences: list[dict]) -> list[dict]:
    """
    Remove duplicate evidences from the normalised list before PDF rendering.

    Deduplication rules (applied in order):
      1. Same content_hash → keep the one with richer metadata.
      2. Same normalised title within same category → keep the better one.

    Preserves original order of the first-seen evidence.
    Only hashes/titles that are non-trivial are compared.
    """
    _TRIVIAL_TITLES = {
        _title_key(t) for t in (
            "نشاط تعليمي موثق بالصورة", "مقطع مرئي تعليمي موثق",
            "تسجيل صوتي تعليمي", "ملاحظة صوتية تعليمية",
            "ملف تعليمي مرفق", "وثيقة تعليمية pdf",
            "مصدر رقمي موثق", "ملاحظة تعليمية موثقة",
            "شاهد تعليمي موثق من المعلم",
        )
    }

    # Pass 1: deduplicate by content_hash
    seen_hashes: dict[str, dict] = {}
    no_hash: list[dict] = []
    for ev in evidences:
        h = (ev.get("content_hash") or "").strip()
        if h:
            if h in seen_hashes:
                seen_hashes[h] = _best_of(seen_hashes[h], ev)
            else:
                seen_hashes[h] = ev
        else:
            no_hash.append(ev)

    deduped_by_hash = list(seen_hashes.values()) + no_hash

    # Pass 2: deduplicate by title within same category (skip trivial titles)
    seen_titles: dict[tuple[str, str], dict] = {}  # (category, title_key) → ev
    result: list[dict] = []
    for ev in deduped_by_hash:
        tk = _title_key(ev.get("title") or "")
        cat = ev.get("category") or ""
        key = (cat, tk)

        if tk in _TRIVIAL_TITLES:
            result.append(ev)   # trivial titles: no title-dedup (all images save individually)
            continue

        if key in seen_titles:
            seen_titles[key] = _best_of(seen_titles[key], ev)
            logger.info(
                "[EXPORT DEDUP] title duplicate skipped: %r in category %r",
                ev.get("title"), cat,
            )
        else:
            seen_titles[key] = ev
            result.append(ev)

    n_removed = len(evidences) - len(result)
    if n_removed:
        logger.info("[EXPORT DEDUP] removed %d duplicate(s) before PDF render", n_removed)

    return result
