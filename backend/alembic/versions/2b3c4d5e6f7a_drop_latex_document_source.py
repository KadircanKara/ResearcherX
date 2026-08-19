"""drop latex_documents.source

The tree in `latex_files` is now the only durable store of a document's
content; `1a2b3c4d5e6f` already backfilled it. Split from that revision on
purpose: the create-and-backfill step is safe to apply while the old code is
still running, and this one is not.

Revision ID: 2b3c4d5e6f7a
Revises: 1a2b3c4d5e6f
Create Date: 2026-08-19
"""

import sqlalchemy as sa
from alembic import op

revision = "2b3c4d5e6f7a"
down_revision = "1a2b3c4d5e6f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("latex_documents", "source")


def downgrade() -> None:
    """Restores the column and refills it from each document's main file.

    FAITHFUL for a single-file document, LOSSY for a multi-file one -- every
    file other than the main file has nowhere to go in a single column. Take a
    dump before running this against a database holding imported projects.
    """
    op.add_column(
        "latex_documents",
        sa.Column("source", sa.Text(), nullable=False, server_default=""),
    )
    op.execute(
        """
        UPDATE latex_documents d
        SET source = COALESCE(f.content, '')
        FROM latex_files f
        WHERE f.document_id = d.id AND f.path = d.main_path
        """
    )
