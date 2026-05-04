"""
Moyasar Payment Gateway integration.

Docs: https://moyasar.com/docs
API base: https://api.moyasar.com/v1

Authentication: HTTP Basic — secret_key as username, empty password.
Amounts: in Halalah (1 SAR = 100 Halalah).
"""
import base64
import hashlib
import hmac
import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

DESCRIPTION_AR = "اشتراك شواهد AI السنوي - عرض الإطلاق"


def _auth_header() -> str:
    """Basic auth header using Moyasar secret key."""
    encoded = base64.b64encode(f"{settings.MOYASAR_SECRET_KEY}:".encode()).decode()
    return f"Basic {encoded}"


def _extract_payment_url(data: dict) -> str | None:
    """
    Flexibly extract the hosted payment URL from a Moyasar invoice response.
    Moyasar returns it under different keys depending on the API version.
    """
    return (
        data.get("url")
        or data.get("payment_url")
        or data.get("invoice_url")
        or (data.get("source") or {}).get("transaction_url")
    )


async def create_invoice(teacher_id: int, teacher_name: str = "") -> dict:
    """
    Create a Moyasar hosted invoice for the launch subscription (29 SAR).

    Returns:
        {
            "provider_payment_id": str,   # Moyasar invoice ID (inv_xxx)
            "payment_url": str,           # URL to send to teacher
            "raw_response": dict,
        }

    Raises:
        ValueError  — if Moyasar does not return a usable payment URL.
        httpx.HTTPStatusError — on API errors (4xx/5xx).
    """
    if not settings.MOYASAR_SECRET_KEY:
        raise ValueError("MOYASAR_SECRET_KEY is not configured")

    callback_url = (
        f"{settings.effective_base_url}/payment/success"
        f"?teacher_id={teacher_id}"
    )

    payload = {
        "amount": settings.SHAWAHID_LAUNCH_PRICE_HALALAH,
        "currency": "SAR",
        "description": DESCRIPTION_AR,
        "back_url": callback_url,
        "metadata": {
            "teacher_id": str(teacher_id),
            "plan_slug": "launch_annual_29",
            "source": "shawahid-service",
        },
    }
    if teacher_name:
        payload["metadata"]["teacher_name"] = teacher_name

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
        "payment_url": payment_url,
        "raw_response": data,
    }


def verify_webhook_signature(raw_body: bytes, signature_header: str) -> bool:
    """
    Verify the Moyasar webhook signature using HMAC-SHA256.

    Moyasar sends the signature in the `Moyasar-Signature` header as a
    hex-encoded HMAC-SHA256 digest of the raw request body, signed with
    MOYASAR_WEBHOOK_SECRET.

    If MOYASAR_WEBHOOK_SECRET is not set, logs a warning and returns True
    (allows dev/testing without secret configured).
    """
    if not settings.MOYASAR_WEBHOOK_SECRET:
        logger.warning(
            "MOYASAR_WEBHOOK_SECRET not set — skipping webhook signature verification"
        )
        return True

    if not signature_header:
        logger.warning("Moyasar-Signature header missing from webhook request")
        return False

    expected = hmac.new(
        settings.MOYASAR_WEBHOOK_SECRET.encode(),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature_header.strip())


def parse_webhook_payload(payload: dict) -> dict | None:
    """
    Normalise a Moyasar webhook payload to a flat dict:
        {
            "payment_id": str,
            "status": str,          # "paid" | "failed" | "authorized" | ...
            "amount_halalah": int,
            "teacher_id": int | None,
            "plan_slug": str | None,
            "raw": dict,
        }

    Returns None if the payload cannot be parsed or has no teacher context.
    Handles both wrapped ({type, data}) and unwrapped (direct payment/invoice) formats.
    """
    # Unwrap event envelope if present
    data: dict = payload.get("data", payload)

    status: str = data.get("status", "")
    payment_id: str = data.get("id", "")
    amount: int = int(data.get("amount", 0))

    metadata: dict = data.get("metadata") or {}
    teacher_id_raw = metadata.get("teacher_id")
    plan_slug: str = metadata.get("plan_slug", "launch_annual_29")

    if not teacher_id_raw:
        logger.info("Moyasar webhook: no teacher_id in metadata — skipping | id=%s", payment_id)
        return None

    try:
        teacher_id = int(teacher_id_raw)
    except (ValueError, TypeError):
        logger.error("Moyasar webhook: invalid teacher_id=%r — skipping", teacher_id_raw)
        return None

    return {
        "payment_id": payment_id,
        "status": status,
        "amount_halalah": amount,
        "teacher_id": teacher_id,
        "plan_slug": plan_slug,
        "raw": data,
    }
