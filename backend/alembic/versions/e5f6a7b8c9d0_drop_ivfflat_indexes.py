"""drop ivfflat indexes — they silently truncate retrieval

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-08-05 00:00:00.000000

IVFFlat is an APPROXIMATE index. It was created `WITH (lists = 100)` while the
tables hold tens to thousands of rows, and Postgres defaults `ivfflat.probes`
to 1 — so one query scans one of a hundred lists and most rows are never
examined at all. They are not ranked low; they are invisible.

Measured on the real dev corpus, one paper of 18 chunks:

    ivfflat.probes=1 (default) ->  3 of 18 chunks visible
    ivfflat.probes=100         -> 18 of 18
    index dropped (exact)      -> 18 of 18

And on the real production query shape (`WHERE paper_id = … ORDER BY <=> LIMIT 5`):

    with the index    1.01 ms, returned 2 rows
    without it        0.24 ms, returned 5 rows

The index was slower AND wrong. It never helped, because retrieval already
filters `WHERE paper_id = :paper_id`, which the btree `ix_paper_chunk_embeddings_paper_id`
reduces to tens of rows; a top-N heapsort over those is sub-millisecond.
Corpus-wide exact search measures 2.8 ms at 1k rows and 20 ms at 5k, against an
LLM turn that costs ~2000 ms.

This supersedes b2c3d4e5f6a7, which restored these indexes after an unadjusted
autogenerate dropped them. That migration was right that autogenerate must not
silently drop them; it was wrong that they should exist.

Revisit only if a single paper grows past ~10k chunks, and then prefer HNSW
(pgvector >= 0.5, available here at 0.8.2) over IVFFlat — HNSW has no
`probes` cliff. Validate any approximate index's recall against exact ground
truth on REAL embeddings before adopting it. Do not benchmark recall with
random vectors: in 768 dimensions random points concentrate at near-identical
distances, so top-k is arbitrary and the measurement is meaningless.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS paper_chunk_embeddings_embedding_idx")
    op.execute("DROP INDEX IF EXISTS conversation_message_embeddings_embedding_idx")


def downgrade() -> None:
    # Restores the pre-existing (approximate, recall-losing) indexes so the
    # chain is reversible. Downgrading reintroduces the truncation bug.
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
