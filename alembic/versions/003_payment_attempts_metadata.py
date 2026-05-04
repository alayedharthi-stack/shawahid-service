"""Add metadata JSONB column to payment_attempts

Revision ID: 003
Revises: 002
Create Date: 2026-05-04

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "payment_attempts",
        sa.Column("metadata", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("payment_attempts", "metadata")
