from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


from app.db.models import PaperSource
from app.schemas.user import UserOut


class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    topic_keywords: list[str] = []


class ProjectUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    topic_keywords: list[str] | None = None


class MemberCreate(BaseModel):
    user_id: str
    role: Literal["editor", "commenter", "viewer"]


class MemberRoleUpdate(BaseModel):
    role: Literal["editor", "commenter", "viewer"]


class MemberOut(BaseModel):
    user: UserOut
    role: str
    model_config = {"from_attributes": True}


class Counts(BaseModel):
    members: int
    papers: int
    chats: int


class ProjectOut(BaseModel):
    id: str
    title: str
    description: str | None
    topic_keywords: list[str]
    my_role: str
    counts: Counts
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class ProjectDetailOut(BaseModel):
    project: ProjectOut
    members: list[MemberOut]
    my_role: str
    model_config = {"from_attributes": True}


class PaperCreate(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    abstract: str | None = None
    body: str | None = None
    pdf_url: str | None = None
    source: PaperSource = PaperSource.MANUAL


class PaperOut(BaseModel):
    id: str
    project_id: str
    title: str
    abstract: str | None
    body: str | None
    pdf_url: str | None
    source: str
    created_at: datetime
    model_config = {"from_attributes": True}


class PaperIngestUrlRequest(BaseModel):
    url: str = Field(min_length=1)


class SuggestMetaResponse(BaseModel):
    title: str | None
    abstract: str | None
    body: str | None


class SuggestTitleFromUrlResponse(BaseModel):
    title: str | None
    abstract: str | None
    requires_manual: bool
