from pydantic import BaseModel
from datetime import datetime


class PaymentWebhookIn(BaseModel):
    teacher_id: int
    payment_provider: str | None = None
    payment_reference: str | None = None
    amount_sar: float | None = None


class SubscriptionOut(BaseModel):
    id: int
    teacher_id: int
    status: str
    plan_slug: str
    amount_sar: float
    starts_at: datetime | None
    ends_at: datetime | None
    payment_provider: str | None
    payment_reference: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
