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


# ── Interactive button sender ─────────────────────────────────────────────────

async def send_whatsapp_button(
    to_phone: str,
    body_text: str,
    button_label: str,
    url: str,
    *,
    teacher_id: int | None = None,
) -> bool:
    """
    Send a WhatsApp CTA-URL interactive button message (in-session).
    Shows as: body text + tappable [button_label] button that opens `url`.
    Falls back to plain text if credentials are missing.

    Returns True on success, False on error (never raises).
    """
    if not settings.WHATSAPP_ACCESS_TOKEN or not settings.WHATSAPP_PHONE_NUMBER_ID:
        logger.info(
            "[WhatsApp button stub] teacher_id=%s To=%s | %s → %s",
            teacher_id, to_phone, body_text[:60], url[:60],
        )
        return True

    api_url = (
        f"https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}"
        f"/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    )
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "interactive",
        "interactive": {
            "type": "cta_url",
            "body": {"text": body_text},
            "action": {
                "name": "cta_url",
                "parameters": {
                    "display_text": button_label,
                    "url": url,
                },
            },
        },
    }
    headers = {
        "Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(api_url, json=payload, headers=headers)

        if resp.status_code == 200:
            logger.info(
                "[PAYMENT BUTTON SENT] teacher_id=%s phone=%s", teacher_id, to_phone
            )
            return True

        try:
            resp_data = resp.json()
        except Exception:
            resp_data = {}

        if _is_outside_window_error(resp_data):
            logger.warning(
                "[PAYMENT WHATSAPP SEND FAILED] teacher_id=%s reason=outside_24h_window "
                "context=payment_button code=%s",
                teacher_id,
                resp_data.get("error", {}).get("code"),
            )
        else:
            logger.error(
                "WhatsApp button send failed | teacher_id=%s status=%d body=%s",
                teacher_id, resp.status_code, resp.text[:300],
            )
        return False

    except Exception as exc:
        logger.error(
            "WhatsApp button send exception | teacher_id=%s: %s", teacher_id, exc
        )
        return False


async def send_export_options_buttons(
    to_phone: str,
    *,
    teacher_id: int | None = None,
) -> bool:
    """
    Send WhatsApp Reply Buttons for export mode selection.
    Falls back automatically to plain text numbers if interactive buttons fail.
    """
    fallback = (
        "📘 كيف تحب ملفك؟\n\n"
        "اختر نوع التصدير:\n"
        "1 كامل\n"
        "2 ذكي\n"
        "3 مختصر"
    )

    if not settings.WHATSAPP_ACCESS_TOKEN or not settings.WHATSAPP_PHONE_NUMBER_ID:
        logger.info("[WhatsApp export buttons stub] teacher_id=%s To=%s", teacher_id, to_phone)
        return True

    api_url = (
        f"https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}"
        f"/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    )
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": "📘 كيف تحب ملفك؟\n\nاختر نوع التصدير:"},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": "export_full", "title": "كامل"}},
                    {"type": "reply", "reply": {"id": "export_smart", "title": "ذكي"}},
                    {"type": "reply", "reply": {"id": "export_short", "title": "مختصر"}},
                ]
            },
        },
    }
    headers = {
        "Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(api_url, json=payload, headers=headers)

        if resp.status_code == 200:
            logger.info("[EXPORT OPTIONS BUTTONS SENT] teacher_id=%s phone=%s", teacher_id, to_phone)
            return True

        logger.warning(
            "[EXPORT OPTIONS BUTTONS FAILED] teacher_id=%s status=%d body=%s — falling back to text",
            teacher_id, resp.status_code, resp.text[:300],
        )
    except Exception as exc:
        logger.warning(
            "[EXPORT OPTIONS BUTTONS EXCEPTION] teacher_id=%s error=%s — falling back to text",
            teacher_id, exc,
        )

    return await send_whatsapp_message(
        to_phone,
        fallback,
        teacher_id=teacher_id,
        context="export_options_fallback",
    )


# ── Payment message builders ──────────────────────────────────────────────────

def build_payment_link_message(payment_url: str, teacher_display_name: str = "") -> str:
    """
    Message sent to teacher when a payment link is created.
    This is a session message — no template required.
    """
    greeting = f"مرحبًا {teacher_display_name} 👋" if teacher_display_name else "مرحبًا أستاذ/ة 👋"
    return (
        f"{greeting}\n\n"
        f"لتفعيل اشتراك شواهد AI السنوي ضمن عرض الإطلاق بقيمة "
        f"{settings.SHAWAHID_LAUNCH_PRICE_SAR} ريال فقط، يمكنك الدفع عبر الرابط التالي:\n"
        f"{payment_url}\n\n"
        f"✅ الخدمة مقدمة من نحلة AI\n"
        f"✅ {settings.BUSINESS_VERIFICATION_TEXT}\n"
        f"🌐 الموقع الرسمي: {settings.NAHLA_WEBSITE}\n"
        f"📩 الدعم: {settings.SUPPORT_EMAIL}\n"
        f"📱 للاستفسار: {settings.SUPPORT_PERSON} {settings.SUPPORT_PHONE}\n\n"
        f"بعد الدفع سيتم تفعيل اشتراكك تلقائيًا بإذن الله."
    )


def build_payment_receipt_message(
    teacher_display_name: str,
    teacher_phone: str,
    provider_payment_id: str,
    paid_at: datetime | str | None = None,
    amount_sar: int = LAUNCH_PRICE_SAR,
    plan_slug: str = "launch_annual_29",
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
) -> str:
    """
    Full receipt sent immediately after a successful payment (webhook PAYMENT_PAID).
    Combines the receipt details AND the activation confirmation in one message.
    Includes subscription period (starts_at → ends_at).
    No template — sent as a regular session message.
    """
    plan_ar = "سنوي - عرض الإطلاق" if plan_slug == "launch_annual_29" else plan_slug

    def _fmt(dt: datetime | str | None) -> str:
        if isinstance(dt, datetime):
            return dt.strftime("%Y/%m/%d")
        return str(dt) if dt else "—"

    paid_date_str = _fmt(paid_at) if paid_at else datetime.now(timezone.utc).strftime("%Y/%m/%d")
    starts_str = _fmt(starts_at) if starts_at else paid_date_str
    ends_str   = _fmt(ends_at)   if ends_at   else "—"

    return (
        f"تم استلام دفعتك بنجاح ✅\n"
        f"هذه فاتورة اشتراكك في شواهد AI:\n\n"
        f"الاسم: {teacher_display_name}\n"
        f"رقم الجوال: {teacher_phone}\n"
        f"الخدمة: شواهد AI\n"
        f"الخطة: {plan_ar}\n"
        f"المبلغ: {amount_sar} ريال\n"
        f"رقم العملية: {provider_payment_id}\n"
        f"تاريخ الدفع: {paid_date_str}\n"
        f"بداية الاشتراك: {starts_str}\n"
        f"نهاية الاشتراك: {ends_str}\n"
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
