from sqlalchemy import BigInteger, Text, Numeric, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base


class PaymentAttempt(Base):
    __tablename__ = "payment_attempts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    teacher_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("teachers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(Text, default="moyasar")
    provider_payment_id: Mapped[str | None] = mapped_column(Text, index=True)
    status: Mapped[str] = mapped_column(Text, default="initiated")
    amount_sar: Mapped[float] = mapped_column(Numeric(10, 2), default=29.00)
    payment_url: Mapped[str | None] = mapped_column(Text)
    raw_response: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    teacher = relationship("Teacher", back_populates="payment_attempts")
