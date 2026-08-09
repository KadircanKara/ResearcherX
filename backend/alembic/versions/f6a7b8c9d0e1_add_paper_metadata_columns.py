"""add structured metadata columns to papers

Hand-written on purpose. Autogenerate is unsafe on this schema: it emits a
spurious vector(768) -> Text change for the embedding columns and drops the
ivfflat indexes.

Existing rows get authors='[]' and metadata_source='none' — an unextracted
paper must read as "not looked at yet", not as "has no authors".

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-08-06 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "papers",
        sa.Column("authors", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column("papers", sa.Column("year", sa.Integer(), nullable=True))
    op.add_column("papers", sa.Column("venue", sa.Text(), nullable=True))
    op.add_column(
        "papers",
        sa.Column("metadata_source", sa.String(length=16), nullable=False, server_default="none"),
    )


def downgrade() -> None:
    op.drop_column("papers", "metadata_source")
    op.drop_column("papers", "venue")
    op.drop_column("papers", "year")
    op.drop_column("papers", "authors")
