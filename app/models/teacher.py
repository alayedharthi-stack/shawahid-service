from sqlalchemy import BigInteger, Boolean, String, Text, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base


class Teacher(Base):
    __tablename__ = "teachers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    phone: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(Text)
    subject: Mapped[str | None] = mapped_column(Text)
    stage: Mapped[str | None] = mapped_column(Text)
    grades: Mapped[str | None] = mapped_column(Text)
    school_name: Mapped[str | None] = mapped_column(Text)
    principal_name: Mapped[str | None] = mapped_column(Text)
    region: Mapped[str | None] = mapped_column(Text)           # المنطقة التعليمية (e.g. الرياض)
    education_admin: Mapped[str | None] = mapped_column(Text)  # إدارة التعليم (e.g. إدارة تعليم الرياض)
    welcomed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    welcome_sent_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    voice_hint_sent_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    first_voice_processed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    media_hint_sent_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    followup_sent_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    evidences = relationship("Evidence", back_populates="teacher", cascade="all, delete-orphan")
    exports = relationship("PortfolioExport", back_populates="teacher", cascade="all, delete-orphan")
    subscriptions = relationship("TeacherSubscription", back_populates="teacher", cascade="all, delete-orphan")
    payment_attempts = relationship("PaymentAttempt", back_populates="teacher", cascade="all, delete-orphan", order_by="PaymentAttempt.id.desc()")
