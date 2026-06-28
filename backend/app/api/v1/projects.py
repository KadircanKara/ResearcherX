"""Projects + members router."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import desc, select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.identity import get_current_user
from app.db.models import Paper, ProjectMember, ResearchRun, User
from app.db.session import get_session
from app.schemas.project import (
    Counts,
    MemberCreate,
    MemberOut,
    MemberRoleUpdate,
    PaperCreate,
    PaperIngestUrlRequest,
    PaperOut,
    ProjectCreate,
    ProjectDetailOut,
    ProjectOut,
    ProjectUpdate,
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
    await project_service.require_member(db, project_id, user.id, "viewer")
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
    await project_service.require_member(db, project_id, user.id, "editor")
    paper = Paper(
        project_id=project_id,
        title=data.title,
        abstract=data.abstract,
        pdf_url=data.pdf_url,
    )
    db.add(paper)
    await db.commit()
    await db.refresh(paper)
    return PaperOut.model_validate(paper)


@router.get("/projects/{project_id}/papers", response_model=list[PaperOut])
async def list_papers(
    project_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> list[PaperOut]:
    await project_service.require_member(db, project_id, user.id, "viewer")
    result = await db.execute(
        sa_select(Paper).where(Paper.project_id == project_id).order_by(Paper.created_at)
    )
    return [PaperOut.model_validate(p) for p in result.scalars().all()]


@router.post("/projects/{project_id}/papers/{paper_id}/ingest")
async def ingest_paper(
    project_id: str,
    paper_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> dict:
    await project_service.require_member(db, project_id, user.id, "editor")
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
    await project_service.require_member(db, project_id, user.id, "editor")
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
    n = await ingest(db, paper_id, pdf_bytes)
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
