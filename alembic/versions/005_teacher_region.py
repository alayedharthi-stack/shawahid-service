"""Add region and education_admin fields to teachers

Revision ID: 005
Revises: 004
Create Date: 2026-05-05

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("teachers", sa.Column("region", sa.Text(), nullable=True))
    op.add_column("teachers", sa.Column("education_admin", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("teachers", "education_admin")
    op.drop_column("teachers", "region")
