"""
WhatsApp Cloud API integration.
Sends messages via Meta Graph API when WHATSAPP_ACCESS_TOKEN + WHATSAPP_PHONE_NUMBER_ID are set.
Falls back to logging stub in dev/test mode.
"""
import logging
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


def detect_command(text: str | None) -> str | None:
    if not text:
        return None
    t = text.strip()
    for trigger, cmd in COMMANDS.items():
        if t == trigger:
            return cmd
    return None


async def get_meta_media_url(media_id: str) -> str | None:
    """
    Fetch the temporary download URL for a Meta media object.
    Requires WHATSAPP_ACCESS_TOKEN.
    Returns None if credentials are missing or request fails.
    """
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


async def send_whatsapp_message(to_phone: str, message: str) -> bool:
    """
    Send a WhatsApp text message via Meta Cloud API.
    Falls back to logging stub when credentials are not configured.
    Returns True on success, False on error.
    """
    if not settings.WHATSAPP_ACCESS_TOKEN or not settings.WHATSAPP_PHONE_NUMBER_ID:
        logger.info("[WhatsApp stub] To=%s | %s", to_phone, message)
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
            resp.raise_for_status()
            logger.info("WhatsApp message sent to %s", to_phone)
            return True
    except Exception as exc:
        logger.error("Failed to send WhatsApp message to %s: %s", to_phone, exc)
        return False


# ── Reply builders ────────────────────────────────────────────────────────────

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
        f"لإنشاء ملف الشواهد PDF، فعّل اشتراك عرض الإطلاق بقيمة *{LAUNCH_PRICE_SAR} ريال فقط* للسنة الدراسية الكاملة.\n\n"
        f"رابط الدفع:\n{payment_link}"
    )


def build_export_ready_reply(pdf_url: str) -> str:
    return (
        "✅ تم إنشاء ملف الشواهد بنجاح!\n"
        f"رابط التحميل: {pdf_url}"
    )


def build_evidence_saved_reply() -> str:
    return "✅ تم حفظ الشاهد بنجاح!"


def build_payment_link_message(payment_url: str, teacher_name: str = "") -> str:
    greeting = f"مرحبًا {teacher_name}،\n" if teacher_name else "مرحبًا،\n"
    return (
        f"{greeting}"
        f"لتفعيل اشتراك شواهد AI السنوي ضمن عرض الإطلاق بقيمة *{LAUNCH_PRICE_SAR} ريال فقط*، "
        f"يرجى الدفع عبر الرابط التالي:\n\n"
        f"{payment_url}\n\n"
        f"بعد الدفع سيتم تفعيل اشتراكك تلقائيًا ✅"
    )


def build_subscription_activated_message() -> str:
    return (
        "تم تفعيل اشتراكك في شواهد AI بنجاح ✅\n"
        "يمكنك الآن إنشاء ملف الشواهد PDF عبر إرسال كلمة: *تصدير*"
    )
