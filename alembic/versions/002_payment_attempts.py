"""Add payment_attempts table for Moyasar integration

Revision ID: 002
Revises: 001
Create Date: 2026-05-04

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "payment_attempts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "teacher_id",
            sa.BigInteger(),
            sa.ForeignKey("teachers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.Text(), server_default="moyasar"),
        sa.Column("provider_payment_id", sa.Text()),
        sa.Column("status", sa.Text(), server_default="initiated"),
        sa.Column("amount_sar", sa.Numeric(10, 2), server_default="29.00"),
        sa.Column("payment_url", sa.Text()),
        sa.Column("raw_response", JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_payment_attempts_teacher_id", "payment_attempts", ["teacher_id"])
    op.create_index(
        "idx_payment_attempts_provider_payment_id",
        "payment_attempts",
        ["provider_payment_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_payment_attempts_provider_payment_id", "payment_attempts")
    op.drop_index("idx_payment_attempts_teacher_id", "payment_attempts")
    op.drop_table("payment_attempts")
