"""Project service — all project and membership operations.

Gates:
  - Non-member access → HTTPException(404)  (do NOT reveal existence via 403)
  - Under-ranked member access → HTTPException(403)
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException

from app.core.permissions import can
from app.db.models import Project, ProjectMember, User
from app.schemas.project import ProjectCreate, ProjectUpdate


# ── internal helpers ────────────────────────────────────────────────────────


async def _get_membership(db: AsyncSession, project_id: str, user_id: str) -> ProjectMember | None:
    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def _require_member(
    db: AsyncSession, project_id: str, user_id: str, need: str = "viewer"
) -> ProjectMember:
    """Return the membership or raise 404 (not-a-member) / 403 (under-ranked)."""
    membership = await _get_membership(db, project_id, user_id)
    if membership is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if not can(membership.role, need):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return membership


async def require_member(
    db: AsyncSession, project_id: str, user_id: str, need: str = "viewer"
) -> ProjectMember:
    """Public wrapper around _require_member for use outside this module."""
    return await _require_member(db, project_id, user_id, need)


async def _get_project_or_404(db: AsyncSession, project_id: str) -> Project:
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


async def _get_members(db: AsyncSession, project_id: str) -> list[ProjectMember]:
    result = await db.execute(select(ProjectMember).where(ProjectMember.project_id == project_id))
    return list(result.scalars().all())


# ── public service functions ────────────────────────────────────────────────


async def list_projects(db: AsyncSession, user: User) -> list[dict]:
    """Return projects the caller is a member of, with my_role and counts."""
    result = await db.execute(
        select(ProjectMember, Project)
        .join(Project, ProjectMember.project_id == Project.id)
        .where(ProjectMember.user_id == user.id)
        .order_by(Project.created_at)
    )
    rows = result.all()

    out = []
    for membership, project in rows:
        members = await _get_members(db, project.id)
        out.append(
            {
                "project": project,
                "my_role": membership.role,
                "counts": {
                    "members": len(members),
                    "papers": 0,
                    "chats": 0,
                },
            }
        )
    return out


async def create_project(db: AsyncSession, user: User, data: ProjectCreate) -> Project:
    """Create a project and make the caller the owner member."""
    project = Project(
        owner_id=user.id,
        title=data.title,
        description=data.description,
        topic_keywords=data.topic_keywords,
    )
    db.add(project)
    await db.flush()

    membership = ProjectMember(project_id=project.id, user_id=user.id, role="owner")
    db.add(membership)
    await db.commit()
    await db.refresh(project)
    return project


async def get_project(
    db: AsyncSession, user: User, project_id: str
) -> tuple[Project, list[ProjectMember], str]:
    """Return (project, members, my_role) for any member, else 404."""
    membership = await _require_member(db, project_id, user.id, "viewer")
    project = await _get_project_or_404(db, project_id)
    members = await _get_members(db, project_id)
    return project, members, membership.role


async def update_project(
    db: AsyncSession, user: User, project_id: str, data: ProjectUpdate
) -> Project:
    """Update project fields — requires editor or above."""
    await _require_member(db, project_id, user.id, "editor")
    project = await _get_project_or_404(db, project_id)

    if data.title is not None:
        project.title = data.title
    if data.description is not None:
        project.description = data.description
    if data.topic_keywords is not None:
        project.topic_keywords = data.topic_keywords

    await db.commit()
    await db.refresh(project)
    return project


async def delete_project(db: AsyncSession, user: User, project_id: str) -> None:
    """Delete a project — requires owner."""
    await _require_member(db, project_id, user.id, "owner")
    project = await _get_project_or_404(db, project_id)
    await db.delete(project)
    await db.commit()


ASSIGNABLE_ROLES = ("editor", "commenter", "viewer")


async def add_member(
    db: AsyncSession,
    user: User,
    project_id: str,
    target_user_id: str,
    role: str,
) -> ProjectMember:
    """Add a member (editor/commenter/viewer) — requires owner."""
    await _require_member(db, project_id, user.id, "owner")
    if role not in ASSIGNABLE_ROLES:
        raise HTTPException(status_code=422, detail="Invalid role")

    existing = await _get_membership(db, project_id, target_user_id)
    if existing is not None:
        raise HTTPException(status_code=409, detail="User is already a member")

    membership = ProjectMember(project_id=project_id, user_id=target_user_id, role=role)
    db.add(membership)
    await db.commit()
    await db.refresh(membership)
    return membership


async def update_member_role(
    db: AsyncSession,
    user: User,
    project_id: str,
    target_user_id: str,
    role: str,
) -> ProjectMember:
    """Change a member's role — requires owner."""
    await _require_member(db, project_id, user.id, "owner")
    if role not in ASSIGNABLE_ROLES:
        raise HTTPException(status_code=422, detail="Invalid role")

    membership = await _get_membership(db, project_id, target_user_id)
    if membership is None:
        raise HTTPException(status_code=404, detail="Member not found")

    membership.role = role
    await db.commit()
    await db.refresh(membership)
    return membership


async def remove_member(
    db: AsyncSession,
    user: User,
    project_id: str,
    target_user_id: str,
) -> None:
    """Remove a member — requires owner; refuses if it would remove the last owner."""
    await _require_member(db, project_id, user.id, "owner")

    target = await _get_membership(db, project_id, target_user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Member not found")

    if target.role == "owner":
        # Count remaining owners
        members = await _get_members(db, project_id)
        owner_count = sum(1 for m in members if m.role == "owner")
        if owner_count <= 1:
            raise HTTPException(status_code=400, detail="Cannot remove the last owner")

    await db.delete(target)
    await db.commit()
