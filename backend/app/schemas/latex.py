from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# The compile service offers exactly these two. lualatex was measured and
# dropped: luaotfload demands a writable cache path during format load, which
# a read-only rootfs cannot give it. Pinning the type here rather than taking
# a free string means an unsupported engine is a 422 at the edge instead of a
# silent fallback deep in the service -- and it keeps `engine` out of
# `tree_hash` as an arbitrary user-controlled string.
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
    main_path: str | None = Field(default=None, min_length=1, max_length=400)
    engine: Engine | None = None


class LatexDocumentOut(BaseModel):
    id: str
    project_id: str
    name: str
    main_path: str
    revision: int
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
    # The document revision this build was made from. The client compares it
    # against the document's current revision to know the PDF is stale --
    # numbers it was handed, never a hash it recomputed.
    revision: int | None = None


class SynctexForwardIn(BaseModel):
    line: int = Field(ge=1)
    # Tree-relative. None (the existing single-file frontend's shape) means
    # "the document's main file" -- the route resolves that default, not this
    # schema, because the right default is the LAST COMPILED build's
    # main_path, which this schema has no access to.
    file: str | None = None


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
    # Tree-relative. Present only alongside `line`: a reverse hit whose file
    # is no longer in the document's tree is reported as not found (see
    # synctex_reverse_route), never as a `line` with no `file` to open it in.
    file: str | None = None


class LatexFileOut(BaseModel):
    path: str
    is_binary: bool
    size_bytes: int
    updated_at: datetime
    model_config = {"from_attributes": True}


class LatexTreeOut(BaseModel):
    """The tree plus the quota, in one response.

    The editor shows used-of-cap in the sidebar footer; a cap the user cannot
    see is a cap they hit as an unexplained failure. Sending it with the tree
    avoids a second round trip on every document open.
    """

    files: list[LatexFileOut]
    used_bytes: int
    max_bytes: int


class LatexMutationOut(BaseModel):
    """What every file mutation returns.

    `revision` is here because the client's staleness rule is
    `hasUnsavedEdits || document.revision != compiled.revision`, and the
    moment an autosave lands `hasUnsavedEdits` goes false. Without the new
    revision in this response the client is one bump behind the server and a
    stale PDF reports itself fresh -- the browser-side twin of the
    `tree_hash` bug. The server owns the counter; it reports it, rather than
    the browser keeping a second implementation of the bump rule.

    `used_bytes` rides along so the sidebar's used-of-25MB footer stays
    correct after a write without a second round trip. `file` is None for a
    delete, which has no file left to describe.
    """

    file: LatexFileOut | None = None
    revision: int
    used_bytes: int


class LatexFileContentOut(BaseModel):
    path: str
    content: str


class LatexFileWrite(BaseModel):
    # Bounded in CHARACTERS here purely to reject an absurd body at the edge;
    # the real cap is `latex_file_max_bytes` in UTF-8 bytes, enforced in the
    # service, because a character count is not a byte count.
    content: str = Field(max_length=_MAX_SOURCE_LENGTH * 5)


class LatexFileRename(BaseModel):
    # `from` is a Python keyword, so the field is aliased. populate_by_name
    # lets tests construct it either way.
    from_path: str = Field(alias="from", min_length=1, max_length=400)
    to_path: str = Field(alias="to", min_length=1, max_length=400)
    model_config = {"populate_by_name": True}


class LatexImportOut(BaseModel):
    """What an import returns. Deliberately the document's identity plus the
    two things detection decided, so the client can show "we picked main.tex
    and xelatex" without a second request."""

    id: str
    name: str
    main_path: str
    engine: str
    revision: int
    file_count: int
