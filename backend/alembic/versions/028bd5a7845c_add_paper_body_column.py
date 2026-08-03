"""add paper body column

Revision ID: 028bd5a7845c
Revises: c1d2e3f4a5b6
Create Date: 2026-06-28 19:38:27.496917
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "028bd5a7845c"
down_revision: Union[str, None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("papers", sa.Column("body", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("papers", "body")
