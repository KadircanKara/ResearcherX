"""add latex_files, main_path, revision

Backfills one file per existing document so that every row is consistent with
`main_path`'s default the moment the column exists. `latex_documents.source`
is deliberately LEFT IN PLACE here and dropped in a later revision, so this
migration is safe to apply while the running code still reads it.

Revision ID: 1a2b3c4d5e6f
Revises: c9d0e1f2a3b4
Create Date: 2026-08-19
"""

import sqlalchemy as sa
from alembic import op

revision = "1a2b3c4d5e6f"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "latex_documents",
        sa.Column("main_path", sa.String(length=400), nullable=False, server_default="main.tex"),
    )
    op.add_column(
        "latex_documents",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_table(
        "latex_files",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("path", sa.String(length=400), nullable=False),
        sa.Column("is_binary", sa.Boolean(), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("blob", sa.LargeBinary(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["latex_documents.id"],
            name="fk_latex_files_document_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "path", name="uq_latex_files_document_path"),
        sa.CheckConstraint(
            "(is_binary = true AND blob IS NOT NULL AND content IS NULL)"
            " OR (is_binary = false AND content IS NOT NULL AND blob IS NULL)",
            name="ck_latex_files_content_xor_blob",
        ),
    )
    op.create_index("ix_latex_files_document_id", "latex_files", ["document_id"], unique=False)

    # Backfill. gen_random_uuid() is built in from Postgres 13; the image is
    # pgvector/pgvector:pg16. octet_length gives the UTF-8 byte count, which
    # is what size_bytes means everywhere else.
    op.execute(
        """
        INSERT INTO latex_files
            (id, document_id, path, is_binary, content, blob, size_bytes,
             created_at, updated_at)
        SELECT gen_random_uuid()::text, id, 'main.tex', false, source, NULL,
               octet_length(source), created_at, updated_at
        FROM latex_documents
        """
    )


def downgrade() -> None:
    """Folds each document's main.tex back into `source`.

    FAITHFUL for a single-file document. LOSSY for a genuinely multi-file
    one: every file other than main.tex is dropped on the floor, because
    `source` is one column and there is nowhere for them to go. Stated rather
    than pretended away -- do not run this against a database holding
    imported projects without taking a dump first.
    """
    op.execute(
        """
        UPDATE latex_documents d
        SET source = COALESCE(f.content, '')
        FROM latex_files f
        WHERE f.document_id = d.id AND f.path = d.main_path
        """
    )
    op.drop_index("ix_latex_files_document_id", table_name="latex_files")
    op.drop_table("latex_files")
    op.drop_column("latex_documents", "revision")
    op.drop_column("latex_documents", "main_path")
