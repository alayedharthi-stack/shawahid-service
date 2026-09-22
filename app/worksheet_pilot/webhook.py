"""Narrow adapter; removes handled events, preserving legacy payload remainder."""
import copy
import hashlib
import hmac
import re
from pathlib import Path

from fastapi import HTTPException

from .service import Store, command, run_job
from .transport import MetaTransport


def private_root(settings):
    root = Path(settings.WORKSHEET_PILOT_PRIVATE_DIR).resolve()
    for public in (settings.storage_path.resolve(), Path("app/static").resolve()):
        if root == public or public in root.parents or root in public.parents:
            raise ValueError("pilot_storage_must_be_private")
    return root


async def handle_payload(body, raw, signature, background_tasks, settings):
    if not settings.WORKSHEET_PILOT_ENABLED:
        return body
    # This flag alone grants nothing. Operator must verify the recipient and
    # record the evidence reference outside the public repository before enabling.
    ready = bool(re.fullmatch(r"9665\d{8}", settings.WORKSHEET_PILOT_PHONE)
                 and settings.WORKSHEET_PILOT_VERIFICATION_REF.strip()
                 and settings.WORKSHEET_PILOT_VERIFIED_AT.strip()
                 and settings.WHATSAPP_APP_SECRET
                 and settings.WHATSAPP_PHONE_NUMBER_ID
                 and settings.WHATSAPP_ACCESS_TOKEN)
    if not ready:
        return body
    result = copy.deepcopy(body)
    store = None
    signature_checked = False
    for entry in result.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            if value.get("metadata", {}).get("phone_number_id") != settings.WHATSAPP_PHONE_NUMBER_ID:
                continue
            messages = value.get("messages", [])
            relevant = [m for m in messages if m.get("from") == settings.WORKSHEET_PILOT_PHONE
                        and m.get("type") == "text" and command(m.get("text", {}).get("body", ""))]
            statuses = [s for s in value.get("statuses", []) if s.get("recipient_id") == settings.WORKSHEET_PILOT_PHONE]
            if not relevant and not statuses:
                continue
            if not signature_checked:
                expected = "sha256=" + hmac.new(settings.WHATSAPP_APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
                if not hmac.compare_digest(expected, signature):
                    raise HTTPException(status_code=403, detail="Invalid pilot webhook signature")
                signature_checked = True
            # Only this verified participant's commands are consumed. All other
            # teachers and business numbers retain their original legacy route.
            value["messages"] = [m for m in messages if m not in relevant]
            if store is None:
                store = Store(private_root(settings))
            for status in statuses:
                if status.get("recipient_id") == settings.WORKSHEET_PILOT_PHONE:
                    store.receipt(settings.WORKSHEET_PILOT_PHONE, status)
            for message in relevant:
                if message.get("from") != settings.WORKSHEET_PILOT_PHONE:
                    continue
                message_id = message.get("id")
                if not isinstance(message_id, str) or not message_id or len(message_id) > 512:
                    continue
                _, school = command(message["text"]["body"])
                job = store.claim(settings.WORKSHEET_PILOT_PHONE, message_id, "تركي عايد الحارثي", school)
                if job:
                    background_tasks.add_task(run_job, store, job, settings.WORKSHEET_PILOT_PHONE, MetaTransport(settings))
    return result
