import uuid
from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class StepKind(StrEnum):
    PLAN = "plan"
    SEARCH = "search"
    VALIDATE = "validate"  # planner validating a searcher finding
    SYNTHESIZE = "synthesize"
    CRITIQUE = "critique"


class ResearchRun(Base):
    __tablename__ = "research_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[RunStatus] = mapped_column(String(16), default=RunStatus.PENDING)
    report: Mapped[str | None] = mapped_column(Text, default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL", name="fk_research_runs_project_id"),
        nullable=True,
        default=None,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    steps: Mapped[list["AgentStep"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="AgentStep.created_at"
    )


class AgentStep(Base):
    __tablename__ = "agent_steps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("research_runs.id", ondelete="CASCADE"))
    kind: Mapped[StepKind] = mapped_column(String(16))
    agent_name: Mapped[str] = mapped_column(String(64))
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    run: Mapped[ResearchRun] = relationship(back_populates="steps")


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    # Auth deferred: nullable now so the auth phase is purely additive.
    password_hash: Mapped[str | None] = mapped_column(String(255), default=None)
    avatar_color: Mapped[str] = mapped_column(String(9), default="#2D3FE0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Role(StrEnum):
    OWNER = "owner"
    EDITOR = "editor"
    COMMENTER = "commenter"
    VIEWER = "viewer"


class PaperSource(StrEnum):
    UPLOAD = "upload"
    LINK = "link"
    MANUAL = "manual"


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, default=None)
    topic_keywords: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    members: Mapped[list["ProjectMember"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class ProjectMember(Base):
    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    project: Mapped[Project] = relationship(back_populates="members")


class Paper(Base):
    __tablename__ = "papers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE", name="fk_papers_project_id"),
        index=True,
    )
    title: Mapped[str] = mapped_column(String(512))
    abstract: Mapped[str | None] = mapped_column(Text, default=None)
    # Structured metadata, extracted at ingest. Authors are full display names
    # in the order printed on the paper. `year`/`venue` are null when the paper
    # genuinely states neither — preprints usually don't — and are never
    # inferred from PDF file dates, which record when the file was saved.
    authors: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    year: Mapped[int | None] = mapped_column(Integer(), default=None)
    venue: Mapped[str | None] = mapped_column(Text, default=None)
    # "crossref" | "llm" | "s2" | "none". Diagnostic only — nothing in the UI
    # reads it. It records which papers went through the fallible LLM path, so a
    # future accuracy question can be answered without re-running extraction.
    # "s2" marks papers bulk-loaded from Semantic Scholar metadata by
    # scripts/bulk_ingest_arxiv.py, which skips extraction entirely.
    metadata_source: Mapped[str] = mapped_column(String(16), default="none", server_default="none")
    body: Mapped[str | None] = mapped_column(Text, default=None)
    # Markdown extracted from an uploaded/linked PDF. Deliberately separate from
    # `body`, which carries manual-entry semantics that update_paper enforces
    # with a 422. Storing it makes a future embedding-model change a pure
    # re-embed instead of a re-download.
    extracted_text: Mapped[str | None] = mapped_column(Text, default=None)
    pdf_url: Mapped[str | None] = mapped_column(Text, default=None)
    source: Mapped[str] = mapped_column(
        String(16), default=PaperSource.MANUAL, server_default="manual"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    chunks: Mapped[list["PaperChunkEmbedding"]] = relationship(
        back_populates="paper", cascade="all, delete-orphan"
    )


class ChatConversation(Base):
    __tablename__ = "chat_conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE", name="fk_chat_conversations_project_id"),
        index=True,
    )
    title: Mapped[str] = mapped_column(String(512))
    created_by: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE", name="fk_chat_conversations_created_by"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ChatMessage.created_at",
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey(
            "chat_conversations.id", ondelete="CASCADE", name="fk_chat_messages_conversation_id"
        ),
        index=True,
    )
    role: Mapped[str] = mapped_column(String(16))  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text)
    citations: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    conversation: Mapped[ChatConversation] = relationship(back_populates="messages")
    embedding: Mapped["ConversationMessageEmbedding | None"] = relationship(
        back_populates="message", cascade="all, delete-orphan", uselist=False
    )


class PaperChunkEmbedding(Base):
    __tablename__ = "paper_chunk_embeddings"
    __table_args__ = (UniqueConstraint("paper_id", "chunk_index"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE", name="fk_paper_chunks_paper_id"),
        index=True,
    )
    chunk_index: Mapped[int] = mapped_column(Integer())
    text: Mapped[str] = mapped_column(Text)
    # Declared as Text so SQLite tests (create_all) don't fail on unknown type.
    # The Alembic migration converts this to vector(768) in Postgres.
    embedding: Mapped[str] = mapped_column(Text)
    # Which embedding model produced this vector. Filtering retrieval on this
    # prevents a provider switch from silently mixing two vector spaces in one
    # pgvector index — a failure with no error and no way to detect it after.
    model: Mapped[str] = mapped_column(String(64), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    paper: Mapped[Paper] = relationship(back_populates="chunks")


class ConversationMessageEmbedding(Base):
    __tablename__ = "conversation_message_embeddings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    message_id: Mapped[str] = mapped_column(
        ForeignKey(
            "chat_messages.id", ondelete="CASCADE", name="fk_conv_msg_embeddings_message_id"
        ),
        unique=True,  # one embedding per message
    )
    # Same TEXT/vector pattern as PaperChunkEmbedding.
    embedding: Mapped[str] = mapped_column(Text)
    # Which embedding model produced this vector. Filtering retrieval on this
    # prevents a provider switch from silently mixing two vector spaces in one
    # pgvector index — a failure with no error and no way to detect it after.
    model: Mapped[str] = mapped_column(String(64), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    message: Mapped[ChatMessage] = relationship(back_populates="embedding")
