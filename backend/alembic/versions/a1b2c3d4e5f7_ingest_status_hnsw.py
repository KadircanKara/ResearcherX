"""ingest status, source hash, chunk project_id, halfvec(768) + HNSW

Hand-written on purpose (autogenerate is unsafe on this schema: it cannot see
`tsv`, and it would read `embedding` as the model's Text).

papers gains `status` (pending | ready | failed), `source_hash` (SHA-256 hex
of the uploaded bytes) and `ingest_error` (internal only). Papers that already
have chunks are backfilled to `ready`; the rest stay `pending`. A partial
unique index allows one copy of a given file per project; manual entries have
no hash and are not constrained.

paper_chunk_embeddings gains `project_id`, denormalised from papers so dense
retrieval can filter a project inside the vector index scan without a join.

`embedding` becomes halfvec(768): half the storage per vector, and an HNSW
index over it fits in memory where one over vector(768) is twice the size.
Every raw query that compares against it must cast the query vector to
halfvec(768); pgvector has no halfvec <=> vector operator. Conversation
message embeddings are untouched and stay vector.

This supersedes the "no approximate index" decision in e5f6a7b8c9d0 for
chunks: HNSW has no ivfflat `probes` cliff, and with iterative scans
(pgvector >= 0.8) a filtered query keeps scanning until it fills its LIMIT.

The type change and the index build rewrite a ~650 MB table and take
minutes; run while chat is idle.

Revision ID: a1b2c3d4e5f7
Revises: f2a3b4c5d6e7
Create Date: 2026-10-27 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f7"
down_revision: Union[str, None] = "f2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. papers: status, source_hash, ingest_error.
    op.add_column(
        "papers",
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
    )
    op.add_column("papers", sa.Column("source_hash", sa.String(64), nullable=True))
    op.add_column("papers", sa.Column("ingest_error", sa.Text(), nullable=True))
    op.execute(
        "UPDATE papers SET status = 'ready' WHERE EXISTS "
        "(SELECT 1 FROM paper_chunk_embeddings c WHERE c.paper_id = papers.id)"
    )
    op.create_index(
        "uq_papers_project_source_hash",
        "papers",
        ["project_id", "source_hash"],
        unique=True,
        postgresql_where=sa.text("source_hash IS NOT NULL"),
    )

    # 2. paper_chunk_embeddings.project_id, backfilled from papers.
    op.add_column("paper_chunk_embeddings", sa.Column("project_id", sa.String(36), nullable=True))
    op.execute(
        "UPDATE paper_chunk_embeddings c SET project_id = p.project_id "
        "FROM papers p WHERE p.id = c.paper_id"
    )
    op.alter_column("paper_chunk_embeddings", "project_id", nullable=False)
    op.create_foreign_key(
        "fk_paper_chunks_project_id",
        "paper_chunk_embeddings",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_paper_chunk_embeddings_project_paper",
        "paper_chunk_embeddings",
        ["project_id", "paper_id"],
    )

    # 3. vector(768) -> halfvec(768).
    op.execute(
        "ALTER TABLE paper_chunk_embeddings "
        "ALTER COLUMN embedding TYPE halfvec(768) USING embedding::halfvec(768)"
    )

    # 4. HNSW. 1GB keeps the graph build in memory instead of spilling.
    # The build is forced SERIAL: a parallel HNSW build places its graph in
    # dynamic shared memory sized to maintenance_work_mem, and Docker's
    # default /dev/shm is 64MB, so it failed on the dev stack with "could not
    # resize shared memory segment ... No space left on device". A serial
    # build uses backend-local memory instead. Both settings last for the
    # migration's connection only.
    op.execute("SET maintenance_work_mem = '1GB'")
    op.execute("SET max_parallel_maintenance_workers = 0")
    op.execute(
        "CREATE INDEX ix_paper_chunk_embeddings_hnsw ON paper_chunk_embeddings "
        "USING hnsw (embedding halfvec_cosine_ops) WITH (m = 16, ef_construction = 128)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_paper_chunk_embeddings_hnsw")
    op.execute(
        "ALTER TABLE paper_chunk_embeddings "
        "ALTER COLUMN embedding TYPE vector(768) USING embedding::vector(768)"
    )
    op.drop_index("ix_paper_chunk_embeddings_project_paper", table_name="paper_chunk_embeddings")
    op.drop_constraint("fk_paper_chunks_project_id", "paper_chunk_embeddings", type_="foreignkey")
    op.drop_column("paper_chunk_embeddings", "project_id")
    op.drop_index("uq_papers_project_source_hash", table_name="papers")
    op.drop_column("papers", "ingest_error")
    op.drop_column("papers", "source_hash")
    op.drop_column("papers", "status")
