"""Add onboarding timestamp fields to teachers

Revision ID: 007
Revises: 006
Create Date: 2026-05-05

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("teachers", sa.Column("welcome_sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("teachers", sa.Column("voice_hint_sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("teachers", sa.Column("first_voice_processed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("teachers", sa.Column("media_hint_sent_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("teachers", "media_hint_sent_at")
    op.drop_column("teachers", "first_voice_processed_at")
    op.drop_column("teachers", "voice_hint_sent_at")
    op.drop_column("teachers", "welcome_sent_at")
