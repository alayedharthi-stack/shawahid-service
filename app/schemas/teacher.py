from pydantic import BaseModel
from datetime import datetime


class TeacherUpdate(BaseModel):
    phone: str
    name: str | None = None
    subject: str | None = None
    stage: str | None = None
    grades: str | None = None
    school_name: str | None = None
    principal_name: str | None = None


class TeacherOut(BaseModel):
    id: int
    phone: str
    name: str | None
    subject: str | None
    stage: str | None
    grades: str | None
    school_name: str | None
    principal_name: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
