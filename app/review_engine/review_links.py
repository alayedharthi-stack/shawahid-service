"""
review_links — signed, time-limited review tokens.

The existing Teacher.review_token field stores a plain random token
(``secrets.token_urlsafe(32)``) that is *permanent*. Phase 5 adds a
signed envelope on top so the link the teacher receives in WhatsApp:

    1. Expires after ``DEFAULT_EXPIRES_HOURS`` (default 72 h).
    2. Cannot be forged without the server-side secret key.
    3. Degrades gracefully: ``validate_review_token`` can verify a
       plain legacy token (no dots → accept as-is with no expiry check).

Token format
------------
``{base_token}.{expiry_unix}.{hmac16}``

Where:
    base_token   — the existing ``review_token`` from Teacher table.
    expiry_unix  — Unix timestamp (int) after which the token is invalid.
    hmac16       — first 16 hex chars of HMAC-SHA256(
                       key=secret,
                       msg=f"{base_token}:{expiry_unix}"
                   ).

Hard rules:
    • No ORM / DB / Playwright.
    • All crypto is from the standard library (``hmac``, ``hashlib``).
    • The signing secret is passed as a parameter, not imported from
      settings — this keeps the module testable without a running app.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import time

logger = logging.getLogger(__name__)

DEFAULT_EXPIRES_HOURS: int = 72
_HMAC_DIGEST_CHARS: int = 16   # 8 bytes of entropy → 64-bit collision resistance


def _sign(base_token: str, expiry: int, secret: str) -> str:
    """Return the HMAC-16 signature for ``base_token:expiry``."""
    msg = f"{base_token}:{expiry}".encode()
    key = secret.encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()[:_HMAC_DIGEST_CHARS]


def generate_review_token(
    base_token: str,
    *,
    secret: str,
    expires_in_hours: int = DEFAULT_EXPIRES_HOURS,
) -> str:
    """Wrap ``base_token`` in a signed envelope with an expiry timestamp.

    Returns a string of the form ``{base_token}.{expiry}.{sig}``
    suitable for embedding in a URL query-string or path segment.
    """
    expiry = int(time.time()) + expires_in_hours * 3600
    sig    = _sign(base_token, expiry, secret)
    return f"{base_token}.{expiry}.{sig}"


def generate_review_link(
    base_token: str,
    *,
    base_url: str,
    secret: str,
    expires_in_hours: int = DEFAULT_EXPIRES_HOURS,
) -> str:
    """Return the full ``/review/{token}`` URL with a signed envelope."""
    signed = generate_review_token(
        base_token, secret=secret, expires_in_hours=expires_in_hours
    )
    return f"{base_url.rstrip('/')}/review/{signed}"


def validate_review_token(
    token: str,
    *,
    secret: str,
    now: int | None = None,
) -> tuple[str, bool]:
    """Validate a (possibly signed) review token.

    Returns ``(base_token, is_valid)``.

    Rules:
        • If the token contains no dots it is a plain legacy DB token;
          accept it without expiry/signature checks (is_valid=True) so
          old links keep working after the upgrade.
        • If the token has exactly two dots (three parts) parse, verify
          signature, and check expiry.
        • Any other shape → invalid.
    """
    _now = now if now is not None else int(time.time())

    if "." not in token:
        # Legacy permanent token — accept as-is.
        return token, bool(token)

    parts = token.split(".")
    if len(parts) != 3:
        logger.debug("[REVIEW LINK] malformed token: %r", token)
        return "", False

    base, ts_str, sig = parts

    try:
        expiry = int(ts_str)
    except ValueError:
        logger.debug("[REVIEW LINK] non-integer expiry in token")
        return "", False

    if _now > expiry:
        logger.info("[REVIEW LINK] token expired (expiry=%d, now=%d)", expiry, _now)
        return "", False

    expected = _sign(base, expiry, secret)
    if not hmac.compare_digest(expected, sig):
        logger.warning("[REVIEW LINK] signature mismatch for token %r", token[:20])
        return "", False

    return base, True
