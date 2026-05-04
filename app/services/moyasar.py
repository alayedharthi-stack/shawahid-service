"""
Moyasar Payment Gateway integration — Shawahid Service.

Docs: https://moyasar.com/docs
API base: https://api.moyasar.com/v1

Authentication: HTTP Basic — secret_key as username, empty password.
Amounts: always in Halalah (1 SAR = 100 Halalah).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
For Nahla later (DO NOT implement now — Nahla integration is separate):

    await create_invoice(
        service        = "nahla",
        teacher_id     = ...,  # use tenant_id instead
        teacher_phone  = ...,
        teacher_name   = tenant.store_name,
        description    = f"اشتراك نحلة - متجر {store_name} - Tenant #{tenant_id}",
        extra_metadata = {
            "tenant_id":  str(tenant.id),
            "store_name": tenant.store_name,
            "plan_slug":  plan.slug,
        },
    )

    metadata sent to Moyasar for Nahla:
    {
      "service":    "nahla",
      "tenant_id":  tenant.id,
      "store_name": tenant.store_name,
      "plan_slug":  plan.slug,
    }

    ⚠️  Never touch Nahla code from this service.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import base64
import hashlib
import hmac
import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# ── Service constants ─────────────────────────────────────────────────────────

SHAWAHID_SERVICE_ID = "shawahid"
SHAWAHID_PLAN_SLUG  = "launch_annual_29"
SHAWAHID_PRICE_HALALAH = 2900          # 29 SAR × 100


# ── Private helpers ───────────────────────────────────────────────────────────

def _auth_header() -> str:
    """HTTP Basic auth header using Moyasar secret key."""
    encoded = base64.b64encode(f"{settings.MOYASAR_SECRET_KEY}:".encode()).decode()
    return f"Basic {encoded}"


def _extract_payment_url(data: dict) -> str | None:
    """
    Flexibly extract the hosted payment URL from a Moyasar invoice response.
    Moyasar returns it under different keys depending on API version and source.
    """
    return (
        data.get("url")
        or data.get("payment_url")
        or data.get("invoice_url")
        or (data.get("source") or {}).get("transaction_url")
    )


def _teacher_display_name(teacher_name: str, teacher_phone: str) -> str:
    """
    Return teacher's display name, falling back to phone-based identifier
    if name is not set.
    """
    name = (teacher_name or "").strip()
    if name:
        return name
    phone = (teacher_phone or "").strip()
    return f"معلم رقم {phone}" if phone else "معلم"


# ── Invoice creation ──────────────────────────────────────────────────────────

def _validate_shawahid_params(
    service: str,
    teacher_id: int | None,
    amount_halalah: int,
    plan_slug: str,
) -> None:
    """
    Guard: reject invoice creation if parameters do not match Shawahid requirements.
    Raises ValueError with a clear message so callers can log and abort.
    """
    if service != SHAWAHID_SERVICE_ID:
        raise ValueError(
            f"Unknown service '{service}' — only '{SHAWAHID_SERVICE_ID}' is supported "
            f"in this function. Add a separate function for other services."
        )
    if not teacher_id:
        raise ValueError("teacher_id is required and must be non-zero")
    if amount_halalah != SHAWAHID_PRICE_HALALAH:
        raise ValueError(
            f"Invalid amount {amount_halalah} halalah — "
            f"Shawahid launch plan requires exactly {SHAWAHID_PRICE_HALALAH} halalah"
        )
    if plan_slug != SHAWAHID_PLAN_SLUG:
        raise ValueError(
            f"Invalid plan_slug '{plan_slug}' — expected '{SHAWAHID_PLAN_SLUG}'"
        )


async def create_invoice(
    *,
    service: str,
    teacher_id: int,
    teacher_phone: str = "",
    teacher_name: str = "",
    description: str | None = None,
    amount_halalah: int | None = None,
    plan_slug: str = SHAWAHID_PLAN_SLUG,
) -> dict:
    """
    Create a Moyasar hosted invoice.

    Parameters
    ----------
    service        : Service identifier — must be "shawahid" (see module docstring
                     for future Nahla usage pattern).
    teacher_id     : Database ID of the teacher (stored in metadata.teacher_id).
    teacher_phone  : Normalized E.164 phone number — stored in metadata.
    teacher_name   : Full name; used in description and customer.name.
                     Falls back to "معلم رقم {phone}" if blank.
    description    : Override the auto-generated description (optional).
    amount_halalah : Payment amount in Halalah. Defaults to SHAWAHID_PRICE_HALALAH.
                     Validated to equal 2900 for the Shawahid service.
    plan_slug      : Subscription plan identifier, stored in metadata.

    Returns
    -------
    {
        "provider_payment_id" : str,   # Moyasar invoice ID (inv_xxx)
        "payment_url"         : str,   # Hosted payment page URL
        "raw_response"        : dict,  # Full Moyasar API response
        "metadata"            : dict,  # metadata dict sent to Moyasar
    }

    Raises
    ------
    ValueError              — invalid params or Moyasar returned no payment URL.
    httpx.HTTPStatusError   — Moyasar API 4xx/5xx error.
    """
    if not settings.MOYASAR_SECRET_KEY:
        raise ValueError("MOYASAR_SECRET_KEY is not configured")

    # Resolve defaults
    if amount_halalah is None:
        amount_halalah = SHAWAHID_PRICE_HALALAH

    # ── Validation (refuse anonymous / wrong-service invoices) ────────────────
    _validate_shawahid_params(service, teacher_id, amount_halalah, plan_slug)

    # ── Build display name with fallback ──────────────────────────────────────
    display_name = _teacher_display_name(teacher_name, teacher_phone)

    # ── Description ───────────────────────────────────────────────────────────
    final_description = description or f"اشتراك شواهد AI السنوي - {display_name}"

    # ── Metadata ──────────────────────────────────────────────────────────────
    metadata: dict = {
        "service":      SHAWAHID_SERVICE_ID,
        "teacher_id":   str(teacher_id),
        "teacher_phone": teacher_phone or "",
        "teacher_name": display_name,
        "plan_slug":    plan_slug,
        "amount_sar":   str(amount_halalah // 100),
    }

    # ── Callback URL ──────────────────────────────────────────────────────────
    callback_url = (
        f"{settings.effective_base_url}/payment/success"
        f"?teacher_id={teacher_id}"
    )

    # ── Moyasar API payload ───────────────────────────────────────────────────
    payload: dict = {
        "amount":      amount_halalah,
        "currency":    "SAR",
        "description": final_description,
        "back_url":    callback_url,
        "metadata":    metadata,
        # Moyasar supports a top-level customer object on some plans
        "customer": {
            "name": display_name,
        },
    }

    logger.info(
        "Creating Moyasar invoice: teacher_id=%d display_name='%s' amount=%d",
        teacher_id, display_name, amount_halalah,
    )

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            f"{settings.MOYASAR_API_BASE}/invoices",
            json=payload,
            headers={
                "Authorization": _auth_header(),
                "Content-Type": "application/json",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    payment_url = _extract_payment_url(data)
    if not payment_url:
        logger.error("Moyasar invoice response missing payment URL: %s", data)
        raise ValueError(f"Moyasar did not return a payment URL. Response: {data}")

    logger.info(
        "Moyasar invoice created: id=%s teacher_id=%d url=%s",
        data.get("id"), teacher_id, payment_url,
    )

    return {
        "provider_payment_id": data.get("id"),
        "payment_url":         payment_url,
        "raw_response":        data,
        "metadata":            metadata,
    }


# ── Webhook signature ─────────────────────────────────────────────────────────

def verify_webhook_signature(raw_body: bytes, signature_header: str) -> bool:
    """
    Verify Moyasar webhook HMAC-SHA256 signature.

    • If MOYASAR_VERIFY_SIGNATURES=false  → skip (dev/testing).
    • If MOYASAR_WEBHOOK_SECRET is blank  → skip with warning (dev mode).
    • Otherwise: require matching Moyasar-Signature header.
    """
    if not settings.moyasar_verify_signatures:
        logger.info("Moyasar signature verification disabled (MOYASAR_VERIFY_SIGNATURES=false)")
        return True

    secret = (settings.MOYASAR_WEBHOOK_SECRET or "").strip()
    if not secret:
        logger.warning(
            "MOYASAR_WEBHOOK_SECRET not configured — accepting webhook without signature"
        )
        return True

    if not signature_header or not signature_header.strip():
        logger.warning("Moyasar-Signature header missing — rejecting (secret IS configured)")
        return False

    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header.strip())


# ── Webhook payload parsing ───────────────────────────────────────────────────

def parse_webhook_payload(payload: dict) -> dict | None:
    """
    Normalise a Moyasar webhook payload to a flat dict for processing.

    Handles:
    • Wrapped format:   {"type": "payment_paid", "data": {...}}
    • Unwrapped format: direct payment / invoice object

    Returns None if the payload has no usable Shawahid context (no teacher_id).

    Returned dict keys:
        payment_id     : str
        status         : "paid" | "failed" | "authorized" | ...
        amount_halalah : int
        service        : str  — from metadata.service (used to guard activation)
        teacher_id     : int
        plan_slug      : str
        raw            : dict — the raw data object
    """
    # Unwrap event envelope if present
    data: dict = payload.get("data", payload)

    status: str     = data.get("status", "")
    payment_id: str = data.get("id", "")
    amount: int     = int(data.get("amount", 0))

    metadata: dict  = data.get("metadata") or {}
    service: str    = metadata.get("service", "")
    teacher_id_raw  = metadata.get("teacher_id")
    plan_slug: str  = metadata.get("plan_slug", SHAWAHID_PLAN_SLUG)

    if not teacher_id_raw:
        logger.info(
            "Moyasar webhook: no teacher_id in metadata — skipping | id=%s service=%r",
            payment_id, service,
        )
        return None

    try:
        teacher_id = int(teacher_id_raw)
    except (ValueError, TypeError):
        logger.error("Moyasar webhook: invalid teacher_id=%r — skipping", teacher_id_raw)
        return None

    return {
        "payment_id":     payment_id,
        "status":         status,
        "amount_halalah": amount,
        "service":        service,
        "teacher_id":     teacher_id,
        "plan_slug":      plan_slug,
        "raw":            data,
    }
