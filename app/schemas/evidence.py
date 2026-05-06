from pydantic import BaseModel
from datetime import datetime


class WhatsAppWebhookIn(BaseModel):
    from_phone: str
    text: str | None = None
    media_url: str | None = None
    mime_type: str | None = None
    file_name: str | None = None


class EvidenceUpdate(BaseModel):
    category: str | None = None
    title: str | None = None
    description: str | None = None
    ai_enriched_description: str | None = None
    grade: str | None = None
    subject: str | None = None


class EvidenceOut(BaseModel):
    id: int
    teacher_id: int
    evidence_type: str
    category: str | None
    title: str | None
    description: str | None
    ai_enriched_description: str | None
    message_text: str | None
    media_url: str | None
    storage_path: str | None
    file_name: str | None
    mime_type: str | None
    grade: str | None
    subject: str | None
    ai_status: str
    created_at: datetime

    model_config = {"from_attributes": True}
