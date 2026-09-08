"""structured chunks: section/page on chunks, pages/outline on papers, tsv over section

Hand-written on purpose (autogenerate is unsafe on this schema — see
f6a7b8c9d0e1). Additive and backward compatible: existing chunk rows get
section='[]', section_text='', page=NULL, and the header builder emits no
Section/Page for them, so chat keeps working until the re-index runs.

`tsv` is dropped and recreated so the sparse arm can match a section name
("Reward Function") lexically. It is a generated column; regenerating 4,594
rows is a table rewrite — run while chat is idle.

Revision ID: d0e1f2a3b4c5
Revises: d7fde275b721
Create Date: 2026-09-08 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d0e1f2a3b4c5"
down_revision: Union[str, None] = "d7fde275b721"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_TSV = "to_tsvector('english', text)"
_NEW_TSV = "to_tsvector('english', coalesce(section_text, '') || ' ' || text)"


def _recreate_tsv(expr: str) -> None:
    op.execute("DROP INDEX IF EXISTS ix_paper_chunk_embeddings_tsv")
    op.execute("ALTER TABLE paper_chunk_embeddings DROP COLUMN IF EXISTS tsv")
    op.execute(
        "ALTER TABLE paper_chunk_embeddings ADD COLUMN tsv tsvector "
        f"GENERATED ALWAYS AS ({expr}) STORED"
    )
    op.execute(
        "CREATE INDEX ix_paper_chunk_embeddings_tsv ON paper_chunk_embeddings USING GIN (tsv)"
    )


def upgrade() -> None:
    op.add_column(
        "paper_chunk_embeddings",
        sa.Column("section", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "paper_chunk_embeddings",
        sa.Column("section_text", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column("paper_chunk_embeddings", sa.Column("page", sa.Integer(), nullable=True))
    op.add_column("papers", sa.Column("extracted_pages", sa.JSON(), nullable=True))
    op.add_column("papers", sa.Column("outline", sa.JSON(), nullable=True))
    _recreate_tsv(_NEW_TSV)


def downgrade() -> None:
    _recreate_tsv(_OLD_TSV)
    op.drop_column("papers", "outline")
    op.drop_column("papers", "extracted_pages")
    op.drop_column("paper_chunk_embeddings", "page")
    op.drop_column("paper_chunk_embeddings", "section_text")
    op.drop_column("paper_chunk_embeddings", "section")
