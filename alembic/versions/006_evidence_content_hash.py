"""Add content_hash column to evidences for deduplication

Revision ID: 006
Revises: 005
Create Date: 2026-05-05

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "evidences",
        sa.Column("content_hash", sa.String(64), nullable=True),
    )
    op.create_index("ix_evidences_content_hash", "evidences", ["content_hash"])


def downgrade() -> None:
    op.drop_index("ix_evidences_content_hash", table_name="evidences")
    op.drop_column("evidences", "content_hash")
