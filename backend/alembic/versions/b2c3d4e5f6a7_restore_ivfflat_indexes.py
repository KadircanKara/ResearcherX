"""restore ivfflat vector indexes dropped by 028bd5a7845c

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-04 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 028bd5a7845c was unadjusted autogenerate output and dropped both ivfflat
    # indexes; that file is fixed, but databases already past it need them back.
    # IF NOT EXISTS keeps this a no-op on any database that still has them.
    op.execute(
        "CREATE INDEX IF NOT EXISTS paper_chunk_embeddings_embedding_idx "
        "ON paper_chunk_embeddings USING ivfflat (embedding vector_cosine_ops) "
        "WITH (lists = 100)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS conversation_message_embeddings_embedding_idx "
        "ON conversation_message_embeddings USING ivfflat (embedding vector_cosine_ops) "
        "WITH (lists = 100)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS conversation_message_embeddings_embedding_idx")
    op.execute("DROP INDEX IF EXISTS paper_chunk_embeddings_embedding_idx")
