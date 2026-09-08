from datetime import datetime

from pydantic import BaseModel, Field


class CitationOut(BaseModel):
    """The shape `chat_service` writes into `chat_messages.citations`.

    Nothing validates against this model today -- the column is `list[dict]`
    and `MessageOut` carries it through raw. It is kept because it is the only
    written-down description of that shape, which makes it worth keeping
    TRUE: a field added to the citation dict and not to this model turns the
    one piece of documentation into a lie.

    `section` and `page` are defaulted because citations persisted before
    structured chunking carry neither, and those rows are read back for the
    lifetime of every conversation that already exists.
    """

    n: int
    paper_id: str
    title: str
    chunk_index: int
    snippet: str
    # Snapshots, like chunk_index and snippet. Only `title` is resolved on
    # read (conversation_service.retitle_citations).
    section: list[str] = Field(default_factory=list)
    page: int | None = None


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    # Raw JSON, carried through unvalidated. CitationOut describes the shape
    # chat_service writes, but nothing enforces it at either end -- see that
    # model's docstring.
    citations: list[dict]
    # Paper ids the user scoped this turn to. IDS ONLY, and nothing records
    # which substring of `content` belonged to which id.
    #
    # A rename does NOT relabel mention text, unlike citation chips: chips are
    # relabelled server-side at read time (conversation_service.retitle_citations)
    # because their titles are carried in the citation JSON, but `content` is
    # the literal string the user typed and is never rewritten. After a rename
    # the old title stays in the text and the frontend's highlight simply stops
    # matching it. The ids keep working for SCOPE regardless — that is what
    # they are for.
    mentions: list[str] = Field(default_factory=list)
    created_at: datetime
    model_config = {"from_attributes": True}


class ConversationUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class ConversationOut(BaseModel):
    id: str
    project_id: str
    title: str
    created_by: str
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class ConversationDetailOut(BaseModel):
    id: str
    project_id: str
    title: str
    created_by: str
    created_at: datetime
    updated_at: datetime
    messages: list[MessageOut]
    model_config = {"from_attributes": True}


class ChatRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    # Capped so a crafted request cannot turn the scope into the whole library
    # by another name. Re-enforced here as well as client-side.
    mentioned_paper_ids: list[str] = Field(default_factory=list, max_length=10)
