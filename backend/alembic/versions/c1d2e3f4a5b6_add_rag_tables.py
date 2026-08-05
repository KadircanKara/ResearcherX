"""add_rag_tables

Revision ID: c1d2e3f4a5b6
Revises: 41fef8e9ff63
Create Date: 2026-06-27 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, None] = "41fef8e9ff63"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "papers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("projects.id", ondelete="CASCADE", name="fk_papers_project_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("abstract", sa.Text(), nullable=True),
        sa.Column("pdf_url", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "chat_conversations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey(
                "projects.id", ondelete="CASCADE", name="fk_chat_conversations_project_id"
            ),
            nullable=False,
            index=True,
        ),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column(
            "created_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_chat_conversations_created_by"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(36),
            sa.ForeignKey(
                "chat_conversations.id", ondelete="CASCADE", name="fk_chat_messages_conversation_id"
            ),
            nullable=False,
            index=True,
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("citations", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "paper_chunk_embeddings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "paper_id",
            sa.String(36),
            sa.ForeignKey("papers.id", ondelete="CASCADE", name="fk_paper_chunks_paper_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("paper_id", "chunk_index"),
    )
    # Add vector column separately — pgvector type only after extension created
    op.execute(
        "ALTER TABLE paper_chunk_embeddings ADD COLUMN embedding vector(768) NOT NULL DEFAULT array_fill(0, ARRAY[768])::vector"
    )
    op.execute("ALTER TABLE paper_chunk_embeddings ALTER COLUMN embedding DROP DEFAULT")
    op.execute(
        "CREATE INDEX ON paper_chunk_embeddings USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )

    op.create_table(
        "conversation_message_embeddings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "message_id",
            sa.String(36),
            sa.ForeignKey(
                "chat_messages.id", ondelete="CASCADE", name="fk_conv_msg_embeddings_message_id"
            ),
            nullable=False,
            unique=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute(
        "ALTER TABLE conversation_message_embeddings ADD COLUMN embedding vector(768) NOT NULL DEFAULT array_fill(0, ARRAY[768])::vector"
    )
    op.execute("ALTER TABLE conversation_message_embeddings ALTER COLUMN embedding DROP DEFAULT")
    op.execute(
        "CREATE INDEX ON conversation_message_embeddings USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )


def downgrade() -> None:
    op.drop_table("conversation_message_embeddings")
    op.drop_table("paper_chunk_embeddings")
    op.drop_table("chat_messages")
    op.drop_table("chat_conversations")
    op.drop_table("papers")
