"""add paper source column

Revision ID: a1b2c3d4e5f6
Revises: 028bd5a7845c
Create Date: 2026-08-03 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "028bd5a7845c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "papers",
        sa.Column("source", sa.String(length=16), nullable=False, server_default="manual"),
    )


def downgrade() -> None:
    op.drop_column("papers", "source")
