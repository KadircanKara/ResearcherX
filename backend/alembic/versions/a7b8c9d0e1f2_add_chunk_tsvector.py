"""add generated tsvector + GIN index for hybrid retrieval

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-08-15 00:00:00.000000

Dense-only retrieval buries lexically-exact chunks: measured on the live dev
corpus, the chunk holding a paper's reward table ranked 53rd globally behind
40 chunks from 18 other papers in the same domain, and 771st under a different
phrasing of the same question. This column is the lexical arm that fixes it.

GENERATED ALWAYS ... STORED rather than a trigger or an application-side
write: it backfills every existing row as part of this migration and maintains
itself for every future insert, so `paper_ingest_service` needs no change and
there is no path by which a chunk can exist without its lexical index.

No `model` coupling, unlike `embedding`. A lexical index is not tied to an
embedding space, so switching EMBEDDING_MODEL does not invalidate it and
retrieval's sparse arm needs no `WHERE model = ...` for correctness (it
carries one only to stay scoped identically to the dense arm).

The column is deliberately absent from the SQLAlchemy model -- SQLite
`create_all` in tests cannot take a tsvector -- so `app/db/autogenerate.py`
carries an `include_object` hook keeping autogenerate from proposing to drop
it.

Adding a STORED generated column rewrites the table. Negligible at the current
4.5k rows; revisit if this table reaches 10^6.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE paper_chunk_embeddings "
        "ADD COLUMN IF NOT EXISTS tsv tsvector "
        "GENERATED ALWAYS AS (to_tsvector('english', text)) STORED"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_paper_chunk_embeddings_tsv "
        "ON paper_chunk_embeddings USING GIN (tsv)"
    )


def downgrade() -> None:
    # Dropping the column drops its index with it; the explicit DROP INDEX is
    # kept so a partially-applied upgrade still reverses cleanly.
    op.execute("DROP INDEX IF EXISTS ix_paper_chunk_embeddings_tsv")
    op.execute("ALTER TABLE paper_chunk_embeddings DROP COLUMN IF EXISTS tsv")
