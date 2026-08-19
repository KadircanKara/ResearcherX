from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# The compile service offers exactly these two. lualatex was measured and
# dropped: luaotfload demands a writable cache path during format load, which
# a read-only rootfs cannot give it. Pinning the type here rather than taking
# a free string means an unsupported engine is a 422 at the edge instead of a
# silent fallback deep in the service -- and it keeps `engine` out of
# `source_hash` as an arbitrary user-controlled string.
Engine = Literal["pdflatex", "xelatex"]

# A LaTeX source is text, and 2MB is far beyond any real paper. Bounding it
# here turns an oversized document into a 422 naming the real problem at the
# edge. Left unbounded, the request instead reaches the compile service and
# fails there with a generic "unavailable" message -- measured live, an
# unbounded 6MB source returned exactly that log line, reading as an infra
# problem rather than a size problem the user could act on.
_MAX_SOURCE_LENGTH = 2_000_000


class LatexDocumentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    source: str = Field(default="", max_length=_MAX_SOURCE_LENGTH)
    engine: Engine = "pdflatex"


class LatexDocumentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    source: str | None = Field(default=None, max_length=_MAX_SOURCE_LENGTH)
    engine: Engine | None = None


class LatexDocumentOut(BaseModel):
    id: str
    project_id: str
    name: str
    source: str
    engine: str
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class CompileOut(BaseModel):
    ok: bool
    log: str
    # Present only on success. The client fetches the bytes separately, so a
    # failed compile leaves the previous hash -- and therefore the last good
    # PDF -- untouched on screen.
    pdf_hash: str | None = None


class SynctexForwardIn(BaseModel):
    line: int = Field(ge=1)


class SynctexForwardOut(BaseModel):
    found: bool
    page: int | None = None
    x: float | None = None
    y: float | None = None
    width: float | None = None
    height: float | None = None


class SynctexReverseIn(BaseModel):
    page: int = Field(ge=1)
    x: float
    y: float


class SynctexReverseOut(BaseModel):
    found: bool
    line: int | None = None
