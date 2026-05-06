"""Add is_excluded_from_export to evidences and review_token to teachers

Revision ID: 010
Revises: 009
Create Date: 2026-05-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "evidences",
        sa.Column(
            "is_excluded_from_export",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
    )
    op.add_column(
        "teachers",
        sa.Column("review_token", sa.String(64), nullable=True),
    )
    op.create_index("ix_teachers_review_token", "teachers", ["review_token"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_teachers_review_token", table_name="teachers")
    op.drop_column("teachers", "review_token")
    op.drop_column("evidences", "is_excluded_from_export")
