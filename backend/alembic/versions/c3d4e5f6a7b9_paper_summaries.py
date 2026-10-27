"""paper_summaries

Revision ID: c3d4e5f6a7b9
Revises: b2c3d4e5f6a8
Create Date: 2026-10-27 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b9"
down_revision: Union[str, None] = "b2c3d4e5f6a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "paper_summaries",
        sa.Column("paper_id", sa.String(36), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("keywords", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("model", sa.String(64), nullable=False, server_default=""),
        sa.Column("summary_model", sa.String(128), nullable=True),
        sa.Column("source", sa.String(16), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("summary_ver", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("batch_id", sa.String(64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["paper_id"], ["papers.id"], name="fk_paper_summaries_paper_id", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("paper_id"),
    )
    # Raw SQL: the model declares it Text so SQLite create_all works.
    op.execute("ALTER TABLE paper_summaries ADD COLUMN doc_embedding vector(768)")
    op.create_index("ix_paper_summaries_batch_id", "paper_summaries", ["batch_id"])


def downgrade() -> None:
    op.drop_index("ix_paper_summaries_batch_id", table_name="paper_summaries")
    op.drop_table("paper_summaries")
