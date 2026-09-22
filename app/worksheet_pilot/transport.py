"""Private Meta media upload: no public answer-key URLs, no success stubs."""
import httpx


class MetaTransport:
    def __init__(self, settings):
        self.settings = settings

    async def _post(self, endpoint, **kwargs):
        s = self.settings
        if not s.WHATSAPP_ACCESS_TOKEN or not s.WHATSAPP_PHONE_NUMBER_ID:
            raise RuntimeError("missing_meta_configuration")
        url = f"https://graph.facebook.com/{s.WHATSAPP_API_VERSION}/{s.WHATSAPP_PHONE_NUMBER_ID}/{endpoint}"
        async with httpx.AsyncClient(timeout=40, follow_redirects=False) as client:
            response = await client.post(url, headers={"Authorization": f"Bearer {s.WHATSAPP_ACCESS_TOKEN}"}, **kwargs)
            response.raise_for_status()
            return response.json()

    async def upload(self, data, filename):
        result = await self._post("media", data={"messaging_product": "whatsapp", "type": "application/pdf"}, files={"file": (filename, data, "application/pdf")})
        media_id = result.get("id")
        if not isinstance(media_id, str) or not media_id:
            raise RuntimeError("missing_media_id")
        return media_id

    async def send(self, phone, media_id, filename, caption):
        result = await self._post("messages", json={"messaging_product": "whatsapp", "to": phone, "type": "document", "document": {"id": media_id, "filename": filename, "caption": caption}})
        message_id = result["messages"][0]["id"]
        if not isinstance(message_id, str) or not message_id:
            raise RuntimeError("missing_message_id")
        return message_id
