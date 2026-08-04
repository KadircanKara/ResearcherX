"""add model column to embedding tables

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-08-04 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ("paper_chunk_embeddings", "conversation_message_embeddings"):
        op.add_column(
            table,
            sa.Column("model", sa.String(64), nullable=False, server_default=""),
        )
        # Existing rows were produced by gemini-embedding-001. Backfilling the
        # true value keeps them retrievable until the provider actually changes,
        # at which point they correctly become stale rather than vanishing the
        # instant this migration runs.
        op.execute(f"UPDATE {table} SET model = 'gemini-embedding-001' WHERE model = ''")


def downgrade() -> None:
    op.drop_column("conversation_message_embeddings", "model")
    op.drop_column("paper_chunk_embeddings", "model")
