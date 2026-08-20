"""Projects + members router."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from openai import RateLimitError
from sqlalchemy import desc, select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import palette
from app.core.config import settings
from app.core.identity import get_current_user
from app.core.logging import log
from app.db.models import Paper, PaperChunkEmbedding, PaperSource, ProjectMember, ResearchRun, User
from app.db.session import get_session
from app.schemas.project import (
    Counts,
    MemberCreate,
    MemberOut,
    MemberRoleUpdate,
    PaperChunkOut,
    PaperCreate,
    PaperIngestUrlRequest,
    PaperOut,
    PaperUpdate,
    ProjectCreate,
    ProjectDetailOut,
    ProjectOut,
    ProjectUpdate,
    SuggestMetaResponse,
    SuggestTitleFromUrlResponse,
)
from app.schemas.research import RunOut
from app.schemas.user import UserOut
from app.services import project_service

router = APIRouter(tags=["projects"])


# ── helpers ──────────────────────────────────────────────────────────────────


async def _member_out(membership: ProjectMember, db: AsyncSession) -> MemberOut:
    user = await db.get(User, membership.user_id)
    return MemberOut(user=UserOut.model_validate(user), role=membership.role)


def _project_out(project, my_role: str, members_count: int) -> ProjectOut:
    return ProjectOut(
        id=project.id,
        title=project.title,
        description=project.description,
        topic_keywords=project.topic_keywords,
        # Derived when the column is NULL so the client is handed a usable
        # colour for every project, including the ones created before the
        # column existed.
        color=project.color or palette.color_for(project.id),
        my_role=my_role,
        counts=Counts(members=members_count, papers=0, chats=0),
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


# ── projects ─────────────────────────────────────────────────────────────────


@router.get("/projects", response_model=list[ProjectOut])
async def list_projects(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> list[ProjectOut]:
    rows = await project_service.list_projects(db, user)
    return [_project_out(row["project"], row["my_role"], row["counts"]["members"]) for row in rows]


@router.post("/projects", response_model=ProjectOut, status_code=201)
async def create_project(
    data: ProjectCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> ProjectOut:
    project = await project_service.create_project(db, user, data)
    return _project_out(project, "owner", 1)


@router.get("/projects/{project_id}", response_model=ProjectDetailOut)
async def get_project(
    project_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> ProjectDetailOut:
    project, members, my_role = await project_service.get_project(db, user, project_id)
    member_outs = [await _member_out(m, db) for m in members]
    return ProjectDetailOut(
        project=_project_out(project, my_role, len(members)),
        members=member_outs,
        my_role=my_role,
    )


@router.patch("/projects/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: str,
    data: ProjectUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> ProjectOut:
    project, members, my_role = await project_service.get_project(db, user, project_id)
    project = await project_service.update_project(db, user, project_id, data)
    return _project_out(project, my_role, len(members))


@router.delete("/projects/{project_id}", status_code=204)
async def delete_project(
    project_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> Response:
    await project_service.delete_project(db, user, project_id)
    return Response(status_code=204)


@router.get("/projects/{project_id}/runs", response_model=list[RunOut])
async def list_project_runs(
    project_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> list[RunOut]:
    # "member", not "editor": project sharing is binary. The finer
    # editor/viewer distinction lives on LaTeX documents -- see
    # services/latex_access.py -- because that is the thing users share.
    await project_service.require_member(db, project_id, user.id, "member")
    result = await db.execute(
        sa_select(ResearchRun)
        .where(ResearchRun.project_id == project_id)
        .order_by(desc(ResearchRun.created_at))
        .limit(limit)
        .offset(offset)
    )
    runs = result.scalars().all()
    return [
        RunOut(
            id=r.id,
            question=r.question,
            status=str(r.status),
            report=r.report,
            error=r.error,
            project_id=r.project_id,
            created_at=r.created_at,
            steps=[],
        )
        for r in runs
    ]


# ── papers ───────────────────────────────────────────────────────────────────


@router.post("/projects/{project_id}/papers", response_model=PaperOut, status_code=201)
async def create_paper(
    project_id: str,
    data: PaperCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> PaperOut:
    await project_service.require_member(db, project_id, user.id, "member")
    paper = Paper(
        project_id=project_id,
        title=data.title,
        abstract=data.abstract,
        body=data.body,
        pdf_url=data.pdf_url,
        source=data.source,
    )
    db.add(paper)
    await db.flush()  # assigns paper.id without committing

    if paper.source == PaperSource.MANUAL and (paper.abstract or paper.body):
        from app.services.paper_ingest_service import index_manual

        # index_manual commits — the flushed paper rides along in the same
        # transaction, so an embedding failure rolls back the paper too rather
        # than leaving a durable row whose chunks never got written.
        await index_manual(db, paper.id, paper.abstract, paper.body)
    else:
        await db.commit()

    await db.refresh(paper)
    return PaperOut.model_validate(paper)


@router.patch("/projects/{project_id}/papers/{paper_id}", response_model=PaperOut)
async def update_paper(
    project_id: str,
    paper_id: str,
    data: PaperUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> PaperOut:
    """Update a paper. Extracted content is immutable for upload/link papers."""
    await project_service.require_member(db, project_id, user.id, "member")
    paper = await db.get(Paper, paper_id)
    if paper is None or paper.project_id != project_id:
        raise HTTPException(status_code=404, detail="Paper not found")

    fields = data.model_dump(exclude_unset=True)

    if paper.source != PaperSource.MANUAL and ({"abstract", "body"} & fields.keys()):
        raise HTTPException(
            status_code=422,
            detail="Extracted content cannot be edited on an uploaded or linked paper.",
        )

    if "title" in fields and fields["title"] is None:
        raise HTTPException(status_code=422, detail="Title cannot be null.")

    text_changed = any(
        key in fields and fields[key] != getattr(paper, key) for key in ("abstract", "body")
    )
    for key, value in fields.items():
        setattr(paper, key, value)

    if paper.source == PaperSource.MANUAL and text_changed:
        from app.services.paper_ingest_service import index_manual

        # Same transaction as the text change — see create_paper.
        await index_manual(db, paper.id, paper.abstract, paper.body)
    else:
        await db.commit()

    await db.refresh(paper)
    return PaperOut.model_validate(paper)


@router.delete("/projects/{project_id}/papers/{paper_id}", status_code=204)
async def delete_paper(
    project_id: str,
    paper_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> Response:
    await project_service.require_member(db, project_id, user.id, "member")
    paper = await db.get(Paper, paper_id)
    if paper is None or paper.project_id != project_id:
        raise HTTPException(status_code=404, detail="Paper not found")
    await db.delete(paper)
    await db.commit()
    return Response(status_code=204)


@router.get("/projects/{project_id}/papers", response_model=list[PaperOut])
async def list_papers(
    project_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> list[PaperOut]:
    await project_service.require_member(db, project_id, user.id, "member")
    result = await db.execute(
        sa_select(Paper).where(Paper.project_id == project_id).order_by(Paper.created_at)
    )
    return [PaperOut.model_validate(p) for p in result.scalars().all()]


@router.get(
    "/projects/{project_id}/papers/{paper_id}/chunks/{chunk_index}",
    response_model=PaperChunkOut,
)
async def get_paper_chunk(
    project_id: str,
    paper_id: str,
    chunk_index: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> PaperChunkOut:
    """Full text of one chunk, for the citation hover card.

    Filters on the configured embedding model: chunk indices are only
    meaningful within one model's chunking, so a stale row from a previous
    model must not be served under the same index.
    """
    await project_service.require_member(db, project_id, user.id, "member")
    paper = await db.get(Paper, paper_id)
    if paper is None or paper.project_id != project_id:
        raise HTTPException(status_code=404, detail="Paper not found")

    chunk = (
        await db.execute(
            sa_select(PaperChunkEmbedding).where(
                PaperChunkEmbedding.paper_id == paper_id,
                PaperChunkEmbedding.chunk_index == chunk_index,
                PaperChunkEmbedding.model == settings.embedding_model,
            )
        )
    ).scalar_one_or_none()
    if chunk is None:
        raise HTTPException(status_code=404, detail="Chunk not found")

    return PaperChunkOut(chunk_index=chunk.chunk_index, text=chunk.text, paper_title=paper.title)


@router.post(
    "/projects/{project_id}/papers/suggest-title",
    response_model=SuggestMetaResponse,
)
async def suggest_paper_title(
    project_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> SuggestMetaResponse:
    """Extract title, abstract, and body from PDF bytes via LLM. Fails open."""
    await project_service.require_member(db, project_id, user.id, "member")
    pdf_bytes = await request.body()
    if not pdf_bytes:
        return SuggestMetaResponse(title=None, abstract=None, body=None)
    from app.services.title_extraction_service import extract_meta_from_pdf

    title, abstract, body = await extract_meta_from_pdf(pdf_bytes)
    return SuggestMetaResponse(title=title, abstract=abstract, body=body)


@router.post(
    "/projects/{project_id}/papers/suggest-title-from-url",
    response_model=SuggestTitleFromUrlResponse,
)
async def suggest_paper_title_from_url(
    project_id: str,
    body: PaperIngestUrlRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> SuggestTitleFromUrlResponse:
    """Extract title + abstract via DOI→Crossref → HTML meta tags."""
    await project_service.require_member(db, project_id, user.id, "member")
    from app.services.title_extraction_service import (
        extract_doi,
        extract_meta_from_page,
        extract_title_from_doi,
    )

    doi = extract_doi(body.url)
    crossref_title: str | None = None
    if doi:
        crossref_title = await extract_title_from_doi(doi)

    page_title, page_abstract = await extract_meta_from_page(body.url)

    title = crossref_title or page_title
    abstract = page_abstract
    if title:
        return SuggestTitleFromUrlResponse(title=title, abstract=abstract, requires_manual=False)
    return SuggestTitleFromUrlResponse(title=None, abstract=None, requires_manual=True)


@router.post("/projects/{project_id}/papers/{paper_id}/ingest")
async def ingest_paper(
    project_id: str,
    paper_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> dict:
    await project_service.require_member(db, project_id, user.id, "member")
    paper = await db.get(Paper, paper_id)
    if paper is None or paper.project_id != project_id:
        raise HTTPException(status_code=404, detail="Paper not found")
    pdf_bytes = await request.body()
    from app.services.paper_ingest_service import ingest

    n = await ingest(db, paper_id, pdf_bytes)
    return {"chunks_stored": n}


@router.post("/projects/{project_id}/papers/{paper_id}/ingest-from-url")
async def ingest_paper_from_url(
    project_id: str,
    paper_id: str,
    body: PaperIngestUrlRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> dict:
    await project_service.require_member(db, project_id, user.id, "member")
    paper = await db.get(Paper, paper_id)
    if paper is None or paper.project_id != project_id:
        raise HTTPException(status_code=404, detail="Paper not found")

    from app.services.paper_fetch_service import fetch_pdf, PaywallError
    from app.services.paper_ingest_service import ingest

    try:
        pdf_bytes = await fetch_pdf(body.url)
    except PaywallError:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "paywalled",
                "message": "No open-access version found. Upload the PDF directly.",
            },
        )
    try:
        n = await ingest(db, paper_id, pdf_bytes, source_url=body.url)
    except RateLimitError:
        # The PDF fetched and parsed fine — only the embedding provider is out
        # of quota. A 500 here reads to the user as "this paper is unreachable"
        # and sends them hunting for a problem with the link that isn't there.
        log.warning("ingest_embedding_quota_exhausted", paper_id=paper_id)
        raise HTTPException(
            status_code=503,
            detail={
                "error": "embedding_unavailable",
                "message": "Indexing is temporarily unavailable. Try again later.",
            },
        )
    return {"chunks_stored": n}


# ── members ──────────────────────────────────────────────────────────────────


@router.get("/projects/{project_id}/members", response_model=list[MemberOut])
async def list_members(
    project_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> list[MemberOut]:
    _, members, _ = await project_service.get_project(db, user, project_id)
    return [await _member_out(m, db) for m in members]


@router.post("/projects/{project_id}/members", response_model=MemberOut, status_code=201)
async def add_member(
    project_id: str,
    data: MemberCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> MemberOut:
    membership = await project_service.add_member(db, user, project_id, data.user_id, data.role)
    return await _member_out(membership, db)


@router.patch("/projects/{project_id}/members/{target_user_id}", response_model=MemberOut)
async def update_member_role(
    project_id: str,
    target_user_id: str,
    data: MemberRoleUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> MemberOut:
    membership = await project_service.update_member_role(
        db, user, project_id, target_user_id, data.role
    )
    return await _member_out(membership, db)


@router.delete("/projects/{project_id}/members/{target_user_id}", status_code=204)
async def remove_member(
    project_id: str,
    target_user_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> Response:
    await project_service.remove_member(db, user, project_id, target_user_id)
    return Response(status_code=204)
