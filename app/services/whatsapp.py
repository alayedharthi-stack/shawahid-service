"""
WhatsApp outbound messaging stub.
Wire WHATSAPP_SEND_URL + WHATSAPP_API_TOKEN to use a real provider (Twilio, Meta, 360dialog).
In MVP, replies are logged and returned for the webhook caller to forward.
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


def detect_command(text: str | None) -> str | None:
    if not text:
        return None
    t = text.strip()
    for trigger, cmd in COMMANDS.items():
        if t == trigger:
            return cmd
    return None


async def send_whatsapp_message(to_phone: str, message: str) -> bool:
    """
    Send a WhatsApp message via the configured provider.
    Returns True on success, False on error or when no provider is configured.
    """
    if not settings.WHATSAPP_SEND_URL:
        logger.info("[WhatsApp stub] To %s: %s", to_phone, message)
        return True

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                settings.WHATSAPP_SEND_URL,
                json={"to": to_phone, "body": message},
                headers={"Authorization": f"Bearer {settings.WHATSAPP_API_TOKEN}"},
            )
            resp.raise_for_status()
            return True
    except Exception as exc:
        logger.error("Failed to send WhatsApp message to %s: %s", to_phone, exc)
        return False


def build_my_files_reply(count: int) -> str:
    return (
        f"لديك *{count}* شاهد محفوظ حتى الآن.\n"
        "اكتب *تصدير* لإنشاء ملف الشواهد PDF."
    )


def build_my_data_reply(teacher) -> str:
    return (
        f"*بياناتك الحالية:*\n"
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
        "لإنشاء ملف الشواهد السنوي، يرجى تفعيل الاشتراك بقيمة *49 ريال سعودي*.\n"
        f"رابط الدفع: {payment_link}"
    )


def build_export_ready_reply(pdf_url: str) -> str:
    return f"✅ تم إنشاء ملف الشواهد بنجاح!\nرابط التحميل: {pdf_url}"
