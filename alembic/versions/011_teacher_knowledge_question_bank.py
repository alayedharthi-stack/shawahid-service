"""Teacher knowledge profile, question bank, and exam records

Revision ID: 011
Revises: 010
Create Date: 2026-06-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "teacher_knowledge_profiles",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("teacher_id", sa.BigInteger(), nullable=False),
        sa.Column("default_subject", sa.Text(), nullable=True),
        sa.Column("default_stage", sa.Text(), nullable=True),
        sa.Column("default_grade", sa.Text(), nullable=True),
        sa.Column("default_semester", sa.Text(), nullable=True),
        sa.Column("cliche_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["teacher_id"], ["teachers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("teacher_id"),
    )
    op.create_index(
        "ix_teacher_knowledge_profiles_teacher_id",
        "teacher_knowledge_profiles",
        ["teacher_id"],
        unique=False,
    )

    op.create_table(
        "question_banks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("teacher_id", sa.BigInteger(), nullable=True),
        sa.Column("school_name", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(length=30), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["teacher_id"], ["teachers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_question_banks_teacher_id", "question_banks", ["teacher_id"])
    op.create_index("ix_question_banks_school_name", "question_banks", ["school_name"])
    op.create_index("ix_question_banks_source_type", "question_banks", ["source_type"])

    op.create_table(
        "bank_questions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("bank_id", sa.BigInteger(), nullable=False),
        sa.Column("teacher_id", sa.BigInteger(), nullable=True),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("question_type", sa.String(length=40), nullable=False),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("grade", sa.Text(), nullable=True),
        sa.Column("semester", sa.Text(), nullable=True),
        sa.Column("skill", sa.Text(), nullable=True),
        sa.Column(
            "difficulty",
            sa.String(length=20),
            server_default="medium",
            nullable=False,
        ),
        sa.Column("model_answer", sa.Text(), nullable=True),
        sa.Column("choices", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("marks", sa.Float(), nullable=True),
        sa.Column("source_file_path", sa.Text(), nullable=True),
        sa.Column("source_evidence_id", sa.BigInteger(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["bank_id"], ["question_banks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["teacher_id"], ["teachers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_evidence_id"], ["evidences.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bank_questions_bank_id", "bank_questions", ["bank_id"])
    op.create_index("ix_bank_questions_teacher_id", "bank_questions", ["teacher_id"])
    op.create_index("ix_bank_questions_subject", "bank_questions", ["subject"])
    op.create_index("ix_bank_questions_grade", "bank_questions", ["grade"])
    op.create_index("ix_bank_questions_content_hash", "bank_questions", ["content_hash"])

    op.create_table(
        "exam_records",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("teacher_id", sa.BigInteger(), nullable=False),
        sa.Column("exam_id", sa.String(length=32), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("grade", sa.Text(), nullable=True),
        sa.Column("stage", sa.Text(), nullable=True),
        sa.Column("semester", sa.Text(), nullable=True),
        sa.Column("exam_type", sa.String(length=30), nullable=True),
        sa.Column("variant_label", sa.String(length=20), nullable=True),
        sa.Column("parent_exam_id", sa.String(length=32), nullable=True),
        sa.Column("structured_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_log", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("pdf_path", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["teacher_id"], ["teachers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_exam_records_teacher_id", "exam_records", ["teacher_id"])
    op.create_index("ix_exam_records_exam_id", "exam_records", ["exam_id"])
    op.create_index("ix_exam_records_parent_exam_id", "exam_records", ["parent_exam_id"])


def downgrade() -> None:
    op.drop_index("ix_exam_records_parent_exam_id", table_name="exam_records")
    op.drop_index("ix_exam_records_exam_id", table_name="exam_records")
    op.drop_index("ix_exam_records_teacher_id", table_name="exam_records")
    op.drop_table("exam_records")

    op.drop_index("ix_bank_questions_content_hash", table_name="bank_questions")
    op.drop_index("ix_bank_questions_grade", table_name="bank_questions")
    op.drop_index("ix_bank_questions_subject", table_name="bank_questions")
    op.drop_index("ix_bank_questions_teacher_id", table_name="bank_questions")
    op.drop_index("ix_bank_questions_bank_id", table_name="bank_questions")
    op.drop_table("bank_questions")

    op.drop_index("ix_question_banks_source_type", table_name="question_banks")
    op.drop_index("ix_question_banks_school_name", table_name="question_banks")
    op.drop_index("ix_question_banks_teacher_id", table_name="question_banks")
    op.drop_table("question_banks")

    op.drop_index("ix_teacher_knowledge_profiles_teacher_id", table_name="teacher_knowledge_profiles")
    op.drop_table("teacher_knowledge_profiles")
