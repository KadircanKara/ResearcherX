"""users.auth_subject for Supabase Auth

Hand-written, like every migration here: autogenerate is unsafe on this
schema (vector columns, tsv).

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-09-30 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, None] = "d0e1f2a3b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("auth_subject", sa.String(length=64), nullable=True))
    op.create_index("ix_users_auth_subject", "users", ["auth_subject"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_auth_subject", table_name="users")
    op.drop_column("users", "auth_subject")
