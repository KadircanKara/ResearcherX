from datetime import datetime

from pydantic import BaseModel, Field


class CitationOut(BaseModel):
    n: int
    paper_id: str
    title: str
    chunk_index: int
    snippet: str


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    citations: list[dict]  # raw JSON — CitationOut shape, validated at write time
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
