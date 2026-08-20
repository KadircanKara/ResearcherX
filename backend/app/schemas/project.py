from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


from app.core.palette import PROJECT_COLORS, is_valid
from app.db.models import PaperSource
from app.schemas.user import UserOut


def _validate_color(value: str | None) -> str | None:
    """Reject anything outside the palette.

    A 422 rather than a silent fallback: a colour the client asked for and did
    not get is a bug in the client worth surfacing, and the allowlist is the
    containment for a value that ends up in a `style` attribute.
    """
    if value is None:
        return None
    if not is_valid(value):
        raise ValueError(f"color must be one of {', '.join(PROJECT_COLORS)}")
    return value


class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    topic_keywords: list[str] = []
    color: str | None = None

    _check_color = field_validator("color")(_validate_color)


class ProjectUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    topic_keywords: list[str] | None = None
    color: str | None = None

    _check_color = field_validator("color")(_validate_color)


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
    # Always populated on the way out, even for a row whose column is
    # NULL -- see `_project_out`. The client never has to know the
    # column is nullable, and never derives a colour of its own.
    color: str
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


class PaperUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=512)
    abstract: str | None = None
    body: str | None = None


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


class PaperChunkOut(BaseModel):
    chunk_index: int
    text: str
    paper_title: str
