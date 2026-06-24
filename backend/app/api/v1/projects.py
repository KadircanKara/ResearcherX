"""Projects + members router."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.identity import get_current_user
from app.db.models import ProjectMember, User
from app.db.session import get_session
from app.schemas.project import (
    Counts,
    MemberCreate,
    MemberOut,
    MemberRoleUpdate,
    ProjectCreate,
    ProjectDetailOut,
    ProjectOut,
    ProjectUpdate,
)
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
