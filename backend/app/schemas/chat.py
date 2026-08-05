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
