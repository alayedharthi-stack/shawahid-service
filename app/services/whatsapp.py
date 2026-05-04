"""
WhatsApp Cloud API integration — Shawahid Service.

Sends messages via Meta Graph API when credentials are configured.
Falls back to a logging stub in dev/test mode (no credentials needed).

Payment notifications flow:
  1. Payment link created → build_payment_link_message()  → send via WA
  2. Payment paid (webhook) → build_payment_receipt_message() → send via WA
  3. No email, no Moyasar notifications, no WhatsApp Templates.

Fallback when outside 24-hour WA session window:
  Log [PAYMENT WHATSAPP SEND FAILED] teacher_id=... reason=outside_24h_window
  Do NOT raise — caller continues normally.
"""
import logging
from datetime import datetime, timezone
import httpx
from app.core.config import settings

logger = logging.getLogger(__name__)

COMMANDS = {
    "ملفي": "my_files",
    "تصدير": "export",
    "بياناتي": "my_data",
    "تعديل بياناتي": "edit_data",
}

LAUNCH_PRICE_SAR = 29

# Meta error codes that indicate the 24-hour messaging window has expired.
# 131026 — Message Undeliverable (includes expired session window).
# 131047 — Re-engagement message requires an approved template.
_OUTSIDE_WINDOW_CODES = frozenset({131026, 131047})


# ── Command detection ─────────────────────────────────────────────────────────

def detect_command(text: str | None) -> str | None:
    if not text:
        return None
    t = text.strip()
    for trigger, cmd in COMMANDS.items():
        if t == trigger:
            return cmd
    return None


# ── Meta media helper ─────────────────────────────────────────────────────────

async def get_meta_media_url(media_id: str) -> str | None:
    """Fetch the temporary download URL for a Meta media object."""
    if not settings.WHATSAPP_ACCESS_TOKEN:
        logger.warning("WHATSAPP_ACCESS_TOKEN not set — cannot fetch media URL for %s", media_id)
        return None
    try:
        url = f"https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}/{media_id}"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                url,
                headers={"Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}"},
            )
            resp.raise_for_status()
            return resp.json().get("url")
    except Exception as exc:
        logger.error("Failed to get Meta media URL for media_id=%s: %s", media_id, exc)
        return None


# ── Core send function ────────────────────────────────────────────────────────

def _is_outside_window_error(resp_data: dict) -> bool:
    """Return True if the Meta API response indicates the 24h session window expired."""
    error = resp_data.get("error", {})
    code = error.get("code")
    if code in _OUTSIDE_WINDOW_CODES:
        return True
    # Some versions nest the code inside error_data
    error_data = error.get("error_data", {})
    if error_data.get("code") in _OUTSIDE_WINDOW_CODES:
        return True
    return False


async def send_whatsapp_message(
    to_phone: str,
    message: str,
    *,
    teacher_id: int | None = None,
    context: str = "",
) -> bool:
    """
    Send a WhatsApp session text message via Meta Cloud API.

    Args:
        to_phone   : E.164 phone number of the recipient.
        message    : Plain text body.
        teacher_id : Optional — used for structured log entries.
        context    : Optional label for logging (e.g. "payment_link", "receipt").

    Returns True on success, False on any error.
    Never raises — callers should not crash if WA delivery fails.
    """
    if not settings.WHATSAPP_ACCESS_TOKEN or not settings.WHATSAPP_PHONE_NUMBER_ID:
        logger.info("[WhatsApp stub] context=%s To=%s | %s", context, to_phone, message[:80])
        return True

    url = (
        f"https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}"
        f"/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    )
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "text",
        "text": {"preview_url": False, "body": message},
    }
    headers = {
        "Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload, headers=headers)

        if resp.status_code == 200:
            logger.info(
                "WhatsApp message sent | teacher_id=%s context=%s phone=%s",
                teacher_id, context, to_phone,
            )
            return True

        # Non-200 — inspect the body
        try:
            resp_data = resp.json()
        except Exception:
            resp_data = {}

        if _is_outside_window_error(resp_data):
            logger.warning(
                "[PAYMENT WHATSAPP SEND FAILED] teacher_id=%s reason=outside_24h_window "
                "context=%s code=%s",
                teacher_id, context,
                resp_data.get("error", {}).get("code"),
            )
        else:
            logger.error(
                "WhatsApp send failed | teacher_id=%s context=%s status=%d body=%s",
                teacher_id, context, resp.status_code, resp.text[:300],
            )
        return False

    except Exception as exc:
        logger.error(
            "WhatsApp send exception | teacher_id=%s context=%s: %s",
            teacher_id, context, exc,
        )
        return False


# ── Payment message builders ──────────────────────────────────────────────────

def build_payment_link_message(payment_url: str, teacher_display_name: str = "") -> str:
    """
    Message sent to teacher when a payment link is created.
    This is a session message — no template required.
    """
    greeting = f"مرحبًا {teacher_display_name} 👋\n" if teacher_display_name else "مرحبًا 👋\n"
    return (
        f"{greeting}"
        f"هذا رابط سداد اشتراك شواهد AI السنوي بقيمة {LAUNCH_PRICE_SAR} ريال:\n"
        f"{payment_url}"
    )


def build_payment_receipt_message(
    teacher_display_name: str,
    teacher_phone: str,
    provider_payment_id: str,
    paid_at: datetime | str | None = None,
    amount_sar: int = LAUNCH_PRICE_SAR,
    plan_slug: str = "launch_annual_29",
) -> str:
    """
    Full receipt sent immediately after a successful payment (webhook PAYMENT_PAID).
    Combines the receipt details AND the activation confirmation in one message.
    No template — sent as a regular session message.
    """
    plan_ar = "سنوي - عرض الإطلاق" if plan_slug == "launch_annual_29" else plan_slug

    # Format payment date
    if isinstance(paid_at, datetime):
        date_str = paid_at.strftime("%Y/%m/%d %H:%M")
    elif paid_at:
        date_str = str(paid_at)
    else:
        date_str = datetime.now(timezone.utc).strftime("%Y/%m/%d %H:%M")

    return (
        f"تم استلام دفعتك بنجاح ✅\n"
        f"هذه فاتورة اشتراكك في شواهد AI:\n\n"
        f"الاسم: {teacher_display_name}\n"
        f"رقم الجوال: {teacher_phone}\n"
        f"الخدمة: شواهد AI\n"
        f"الخطة: {plan_ar}\n"
        f"المبلغ: {amount_sar} ريال\n"
        f"رقم العملية: {provider_payment_id}\n"
        f"التاريخ: {date_str}\n"
        f"الحالة: ✅ مدفوع\n\n"
        f"تم تفعيل اشتراكك ويمكنك الآن استخدام الخدمة مباشرة 🙌\n"
        f"أرسل كلمة *تصدير* لإنشاء ملف الشواهد PDF."
    )


def build_subscription_activated_message() -> str:
    """Standalone activation nudge (used when receipt was already sent separately)."""
    return (
        "يمكنك الآن إنشاء ملف الشواهد PDF بإرسال كلمة: *تصدير*"
    )


# ── Other reply builders ──────────────────────────────────────────────────────

def build_my_files_reply(count: int, sub_active: bool = False) -> str:
    sub_line = (
        "✅ اشتراكك نشط — اكتب *تصدير* لإنشاء ملف PDF."
        if sub_active
        else f"اكتب *تصدير* لإنشاء ملف PDF (يتطلب اشتراكًا بـ {LAUNCH_PRICE_SAR} ريال)."
    )
    return (
        f"لديك *{count}* شاهد محفوظ حتى الآن.\n"
        f"{sub_line}"
    )


def build_my_data_reply(teacher) -> str:
    return (
        "*بياناتك الحالية:*\n"
        f"الاسم: {teacher.name or '—'}\n"
        f"المادة: {teacher.subject or '—'}\n"
        f"المرحلة: {teacher.stage or '—'}\n"
        f"الصفوف: {teacher.grades or '—'}\n"
        f"المدرسة: {teacher.school_name or '—'}\n"
        f"مدير المدرسة: {teacher.principal_name or '—'}"
    )


def build_edit_data_template() -> str:
    return (
        "يرجى إرسال بياناتك بالصيغة التالية:\n\n"
        "الاسم: \n"
        "المادة: \n"
        "المرحلة: \n"
        "الصفوف: \n"
        "المدرسة: \n"
        "مدير المدرسة: "
    )


def build_subscription_required_reply(payment_link: str) -> str:
    return (
        f"لإنشاء ملف الشواهد PDF، فعّل اشتراك عرض الإطلاق بقيمة *{LAUNCH_PRICE_SAR} ريال فقط* "
        f"للسنة الدراسية الكاملة.\n\n"
        f"رابط الدفع:\n{payment_link}"
    )


def build_export_ready_reply(pdf_url: str) -> str:
    return (
        "✅ تم إنشاء ملف الشواهد بنجاح!\n"
        f"رابط التحميل: {pdf_url}"
    )


def build_evidence_saved_reply() -> str:
    return "✅ تم حفظ الشاهد بنجاح!"
