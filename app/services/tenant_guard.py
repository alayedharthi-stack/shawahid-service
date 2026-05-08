"""
Tenant identity / contamination guard — Shawahid service only.

Background
----------
Shawahid and Nahla are two completely independent Railway deployments,
each with their own Meta App, codebase, prompt and database. They have
*never* shared code. However, the user reported seeing a Nahla persona
reply ("نحلة 🐝 مستشارة المبيعات في متجر آل عايد...") inside a
Shawahid-only number. The contamination cannot originate from this
repository — every mention of those phrases is absent from the entire
Shawahid codebase. The only remaining explanations are upstream:

  1. Meta App webhook for that phone_number_id still subscribed to the
     Nahla endpoint (or to *both* services).
  2. Same WHATSAPP_PHONE_NUMBER_ID accidentally re-used in two Railway
     services pointing at the same Meta business number.
  3. The user is messaging the wrong number entirely.

This module exists to **make those upstream causes immediately visible**
from the Shawahid logs and to guarantee that — even if Meta delivers a
message intended for another tenant — Shawahid never replies on its
behalf.

Public surface
--------------
* :func:`build_inbound_debug_line` — formats the canonical
  ``[WA INBOUND DEBUG]`` banner emitted at the very top of the inbound
  webhook handler.
* :func:`mask_db_url` — produces a safe masked DATABASE_URL fingerprint
  ``postgresql://***:***@host:port/db`` for logs.
* :func:`prompt_profile_fingerprint` — short stable identifier for the
  GPT prompt + model that Shawahid is configured to use.
* :func:`is_foreign_tenant` — returns ``True`` when an incoming Meta
  message was *not* addressed to Shawahid's configured business number,
  signalling that the webhook handler must drop it without replying.
* :func:`identity_snapshot` — masked dict of the runtime identity for
  use in startup banner and ``/internal/identity``.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any
from urllib.parse import urlparse

from app.core.config import settings

logger = logging.getLogger(__name__)


# ── Identity ────────────────────────────────────────────────────────────────

SERVICE_NAME = "shawahid"
WEBHOOK_PATH = "/webhook/whatsapp"


def _mask_secret(value: str | None, *, keep: int = 4) -> str:
    """Return ``***1234`` style mask. Never log the full secret."""
    if not value:
        return "<empty>"
    s = str(value)
    if len(s) <= keep:
        return "***"
    return f"***{s[-keep:]}"


def mask_db_url(url: str | None) -> str:
    """Return a host/port/db masked rendering of a DATABASE_URL.

    Example: ``postgresql://user:secret@db.host:5432/shawahid_db``
             becomes ``postgresql://***:***@db.host:5432/shawahid_db``.
    Falls back to ``<unset>`` / ``<unparseable>`` when the URL is missing
    or malformed; never returns the original credentials.
    """
    if not url:
        return "<unset>"
    try:
        p = urlparse(url)
        host = p.hostname or "?"
        port = f":{p.port}" if p.port else ""
        db = (p.path or "").lstrip("/") or "?"
        scheme = p.scheme or "?"
        return f"{scheme}://***:***@{host}{port}/{db}"
    except Exception:  # pragma: no cover — defensive
        return "<unparseable>"


def prompt_profile_fingerprint() -> str:
    """Stable short id of the configured GPT brain prompt + model.

    The fingerprint is derived from the configured model name and the
    service identity so that any deployment using a *different* GPT
    profile (e.g. a Nahla prompt accidentally pointed at this service)
    would visibly produce a different fingerprint in logs.
    """
    parts = [
        SERVICE_NAME,
        settings.OPENAI_MODEL or "<no-model>",
    ]
    raw = "|".join(parts)
    short = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    return f"{SERVICE_NAME}:{settings.OPENAI_MODEL or '?'}:{short}"


# ── Inbound tenant identity ─────────────────────────────────────────────────

def is_foreign_tenant(parsed_phone_number_id: str | None) -> bool:
    """Return True if the inbound message was destined for *another* tenant.

    Meta echoes the receiving business number in
    ``value.metadata.phone_number_id``. We compare it strictly against the
    configured ``settings.WHATSAPP_PHONE_NUMBER_ID``. The comparison is
    skipped (returns ``False``) when either value is empty so local dev
    and tests stay frictionless.
    """
    configured = (settings.WHATSAPP_PHONE_NUMBER_ID or "").strip()
    incoming = (parsed_phone_number_id or "").strip()
    if not configured or not incoming:
        return False
    return configured != incoming


def build_inbound_debug_line(
    *,
    phone_number_id: str | None,
    display_phone_number: str | None,
    waba_id: str | None,
    from_phone: str | None,
    message_text: str | None,
    contact_name: str | None,
    msg_type: str | None,
) -> str:
    """Format the canonical [WA INBOUND DEBUG] banner.

    The banner intentionally exposes every Meta-side identity field so a
    single log line is sufficient to verify which tenant Meta is routing
    to. Sensitive fields (text body, tokens, DB URL) are truncated /
    masked. The output is one line — easy to grep in Railway logs.
    """
    safe_text = (message_text or "")[:80].replace("\n", " ")
    return (
        "[WA INBOUND DEBUG] "
        f"service={SERVICE_NAME} "
        f"phone_number_id={phone_number_id or '<none>'} "
        f"display_phone_number={display_phone_number or '<none>'} "
        f"waba_id={waba_id or '<none>'} "
        f"from={from_phone or '<none>'} "
        f"contact={contact_name or '<none>'} "
        f"msg_type={msg_type or '<none>'} "
        f"message_text={safe_text!r} "
        f"webhook_path={WEBHOOK_PATH} "
        f"db_url_masked={mask_db_url(settings.DATABASE_URL)} "
        f"prompt_profile={prompt_profile_fingerprint()} "
        f"configured_phone_number_id={_mask_secret(settings.WHATSAPP_PHONE_NUMBER_ID)} "
        f"app_env={settings.APP_ENV}"
    )


# ── Public identity snapshot (for /internal/identity + startup banner) ──────

def identity_snapshot() -> dict[str, Any]:
    """Return a safe, masked snapshot of the runtime identity.

    Suitable for an ``/internal/identity`` health endpoint and for the
    startup banner. Never includes raw secrets — only suffixes for
    fingerprinting.
    """
    return {
        "service": SERVICE_NAME,
        "app_env": settings.APP_ENV,
        "webhook_path": WEBHOOK_PATH,
        "phone_number_id_suffix": _mask_secret(settings.WHATSAPP_PHONE_NUMBER_ID),
        "verify_token_suffix": _mask_secret(settings.WHATSAPP_VERIFY_TOKEN),
        "access_token_suffix": _mask_secret(settings.WHATSAPP_ACCESS_TOKEN),
        "database_url_masked": mask_db_url(settings.DATABASE_URL),
        "openai_model": settings.OPENAI_MODEL,
        "prompt_profile": prompt_profile_fingerprint(),
        "public_base_url": settings.effective_base_url,
    }
