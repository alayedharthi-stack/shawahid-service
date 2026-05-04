"""Initial schema: teachers, evidences, portfolio_exports, teacher_subscriptions

Revision ID: 001
Revises:
Create Date: 2026-05-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "teachers",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("phone", sa.String(20), unique=True, nullable=False),
        sa.Column("name", sa.Text()),
        sa.Column("subject", sa.Text()),
        sa.Column("stage", sa.Text()),
        sa.Column("grades", sa.Text()),
        sa.Column("school_name", sa.Text()),
        sa.Column("principal_name", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "evidences",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("teacher_id", sa.BigInteger(), sa.ForeignKey("teachers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_phone", sa.String(20), nullable=False),
        sa.Column("evidence_type", sa.String(30), nullable=False),
        sa.Column("category", sa.String(80)),
        sa.Column("title", sa.Text()),
        sa.Column("description", sa.Text()),
        sa.Column("message_text", sa.Text()),
        sa.Column("media_url", sa.Text()),
        sa.Column("storage_path", sa.Text()),
        sa.Column("file_name", sa.Text()),
        sa.Column("mime_type", sa.Text()),
        sa.Column("grade", sa.Text()),
        sa.Column("subject", sa.Text()),
        sa.Column("ai_status", sa.String(30), server_default="pending"),
        sa.Column("ai_raw", JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_evidences_teacher_id", "evidences", ["teacher_id"])
    op.create_index("idx_evidences_teacher_category", "evidences", ["teacher_id", "category"])
    op.create_index("idx_evidences_teacher_created", "evidences", ["teacher_id", "created_at"])

    op.create_table(
        "portfolio_exports",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("teacher_id", sa.BigInteger(), sa.ForeignKey("teachers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("pdf_url", sa.Text()),
        sa.Column("storage_path", sa.Text()),
        sa.Column("status", sa.String(30), server_default="pending"),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_exports_teacher_id", "portfolio_exports", ["teacher_id"])

    op.create_table(
        "teacher_subscriptions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("teacher_id", sa.BigInteger(), sa.ForeignKey("teachers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(30), server_default="inactive"),
        sa.Column("plan_slug", sa.String(50), server_default="annual_49"),
        sa.Column("amount_sar", sa.Numeric(10, 2), server_default="49.00"),
        sa.Column("starts_at", sa.DateTime(timezone=True)),
        sa.Column("ends_at", sa.DateTime(timezone=True)),
        sa.Column("payment_provider", sa.Text()),
        sa.Column("payment_reference", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_subscriptions_teacher_id", "teacher_subscriptions", ["teacher_id"])


def downgrade() -> None:
    op.drop_table("teacher_subscriptions")
    op.drop_table("portfolio_exports")
    op.drop_index("idx_evidences_teacher_created", "evidences")
    op.drop_index("idx_evidences_teacher_category", "evidences")
    op.drop_index("idx_evidences_teacher_id", "evidences")
    op.drop_table("evidences")
    op.drop_table("teachers")
