"""usage_events for metered actions with no row of their own

Hand-written (autogenerate is unsafe on this schema).

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-09-30 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f2a3b4c5d6e7"
down_revision: Union[str, None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "usage_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_usage_events_user_id"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_usage_events_user_id", "usage_events", ["user_id"])
    op.create_index("ix_usage_events_created_at", "usage_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_usage_events_created_at", table_name="usage_events")
    op.drop_index("ix_usage_events_user_id", table_name="usage_events")
    op.drop_table("usage_events")
