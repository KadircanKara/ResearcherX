import uuid
from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
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
    MEMBER = "member"


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
    # Nullable because every project that predates this column has no colour
    # and must not be back-filled: `palette.color_for` derives a stable one
    # from the id, so a NULL is a working default rather than a gap.
    color: Mapped[str | None] = mapped_column(String(7), default=None)
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
    # The URL that actually SERVED the PDF, when it differs from the one the
    # user pasted. `paper_fetch_service.fetch_pdf` falls back to Unpaywall or
    # Semantic Scholar on a 403, so ingestion can succeed while `pdf_url`
    # still points at a paywall -- sending the reader to the exact wall the
    # server worked around. Null when the pasted URL served the file itself,
    # which is the common case.
    resolved_pdf_url: Mapped[str | None] = mapped_column(Text, default=None)
    source: Mapped[str] = mapped_column(
        String(16), default=PaperSource.MANUAL, server_default="manual"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    chunks: Mapped[list["PaperChunkEmbedding"]] = relationship(
        back_populates="paper", cascade="all, delete-orphan"
    )


class LatexDocument(Base):
    """One LaTeX document in a project.

    The tree in `latex_files` is the ONLY durable artifact -- there is no
    `source` column on this table any more. The PDF and the SyncTeX map are
    derived, cached in memory, and regenerated on a miss -- see
    services/latex_cache.py. The API's `source` field is a compatibility
    shim resolved from and written to the main file at request time.
    """

    __tablename__ = "latex_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE", name="fk_latex_documents_project_id"),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200))
    # String(16), not Enum(): adding an engine needs no migration, exactly as
    # StepKind/RunStatus already work in this schema.
    engine: Mapped[str] = mapped_column(String(16), default="pdflatex", server_default="pdflatex")
    # The file compiled for this document. A relative path into `latex_files`,
    # validated on write to exist in the tree and end in .tex.
    main_path: Mapped[str] = mapped_column(
        String(400), default="main.tex", server_default="main.tex"
    )
    # Bumped on EVERY file mutation -- write, create, delete, rename, binary
    # upload. This is what tells the editor its PDF is stale, and it has to
    # move for a delete and a binary upload as well as an edit; a content
    # comparison over the open buffers would miss both. It is NOT optimistic
    # concurrency: nothing checks it on write, so it does not prevent two
    # members clobbering each other.
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", name="fk_latex_documents_created_by"),
        default=None,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class LatexDocumentMember(Base):
    """An explicit editor/viewer grant on ONE LaTeX document.

    Project membership answers whether someone may see a project at all; this
    table answers whether they may change a particular document in it. There
    is no row here for the project owner or the document creator: both
    short-circuit ahead of this lookup in `services/latex_access.py`, so such
    a row could never take effect, and the grant routes refuse to create one.
    """

    __tablename__ = "latex_document_members"
    __table_args__ = (UniqueConstraint("document_id", "user_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(
        ForeignKey(
            "latex_documents.id",
            ondelete="CASCADE",
            name="fk_latex_document_members_document_id",
        ),
        index=True,
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE", name="fk_latex_document_members_user_id"),
    )
    # String(16) over a Python-side StrEnum, like StepKind and RunStatus:
    # adding a level later needs no migration.
    role: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class LatexFile(Base):
    """One file inside a LaTeX document's tree.

    Text and binary content live in SEPARATE columns so "is this text?" is
    decided exactly once, at ingest, by the code that can look at the bytes
    properly. A single BYTEA column would push that decision onto every
    reader and put a UTF-8 decode on the editor's hot path -- the path that
    was a plain Text column when a document was one `source` string, and
    should stay one.

    The CHECK constraint is what stops the pair drifting into a third state
    (both set, or neither) that no reader handles.

    `size_bytes` is STORED, not computed. Quota enforcement runs on every
    write; SUM over an indexed integer is a lookup, while
    SUM(length(content) + length(blob)) detoasts the whole project on every
    autosave.
    """

    __tablename__ = "latex_files"
    __table_args__ = (
        UniqueConstraint("document_id", "path", name="uq_latex_files_document_path"),
        CheckConstraint(
            "(is_binary = true AND blob IS NOT NULL AND content IS NULL)"
            " OR (is_binary = false AND content IS NOT NULL AND blob IS NULL)",
            name="ck_latex_files_content_xor_blob",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("latex_documents.id", ondelete="CASCADE", name="fk_latex_files_document_id"),
        index=True,
    )
    path: Mapped[str] = mapped_column(String(400))
    is_binary: Mapped[bool] = mapped_column(Boolean, default=False)
    content: Mapped[str | None] = mapped_column(Text, default=None)
    blob: Mapped[bytes | None] = mapped_column(LargeBinary, default=None)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
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
    # Paper ids the user explicitly scoped this turn to (see
    # docs — "@" mentions). Empty list means unscoped: retrieval was global.
    mentions: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    conversation: Mapped[ChatConversation] = relationship(back_populates="messages")
    embedding: Mapped["ConversationMessageEmbedding | None"] = relationship(
        back_populates="message", cascade="all, delete-orphan", uselist=False
    )


class PaperFile(Base):
    """The uploaded PDF itself, kept so a user can get their own file back.

    A SIDE TABLE rather than a column on `papers`, deliberately. A
    `LargeBinary` on `papers` is loaded by every `db.get(Paper, ...)` --
    including the chat retrieval path, which reads papers on every turn --
    unless it is deferred, and a deferred column that someone later
    un-defers is a silent performance cliff no test would catch. A separate
    table cannot be loaded by accident.

    Only UPLOADED papers get a row. A link-sourced paper has a canonical
    home on the web and `papers.pdf_url` already points at it; storing a
    second copy buys nothing the link does not.

    `size_bytes` is stored rather than derived from `length(blob)`, the same
    reason `latex_files` stores it: computing it detoasts the blob.
    """

    __tablename__ = "paper_files"

    paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE", name="fk_paper_files_paper_id"),
        primary_key=True,
    )
    blob: Mapped[bytes] = mapped_column(LargeBinary)
    size_bytes: Mapped[int] = mapped_column(Integer())
    content_type: Mapped[str] = mapped_column(String(128), default="application/pdf")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


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
    # NOT DECLARED HERE, on purpose: `tsv`, a generated tsvector column with a
    # GIN index, added by migration a7b8c9d0e1f2 for hybrid retrieval's sparse
    # arm. SQLite `create_all` in tests cannot take a tsvector, and nothing
    # reads it through the ORM -- retrieval uses raw text() SQL. The guard that
    # stops autogenerate proposing to drop it lives in app/db/autogenerate.py
    # (`include_object`), pinned by tests/test_autogenerate_exclusions.py.

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
