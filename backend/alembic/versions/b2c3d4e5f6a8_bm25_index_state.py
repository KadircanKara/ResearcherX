"""bm25_index_state: per-project state of the bm25s sparse index

One row per project: which on-disk index version is live, when the first
unindexed chunk change landed (`dirty_since`) and the latest one
(`last_change_at`). Hand-written; autogenerate cannot see `tsv` on the chunks
table.

Projects that already hold chunks are backfilled as dirty so the first
background build covers them; projects with none get no row until a chunk
write marks them.

Revision ID: b2c3d4e5f6a8
Revises: a1b2c3d4e5f7
Create Date: 2026-10-27 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a8"
down_revision: Union[str, None] = "a1b2c3d4e5f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bm25_index_state",
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("current_version", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dirty_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_change_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokenizer_ver", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_bm25_state_project_id", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("project_id"),
    )
    op.execute(
        "INSERT INTO bm25_index_state (project_id, dirty_since, last_change_at) "
        "SELECT DISTINCT project_id, now(), now() FROM paper_chunk_embeddings"
    )


def downgrade() -> None:
    op.drop_table("bm25_index_state")
