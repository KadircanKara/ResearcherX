"""Map a verified Supabase identity to our User, creating it on first login.

Lookup order: the `sub` we stored, then the verified email (case-insensitive).
`users.email` is unique and only the operator can create Supabase users, so an
email match is the same person — including a client deleted and re-invited
in Supabase, who arrives with a NEW `sub` and is relinked to their data.

A new user gets exactly one project, owned by them. Two first requests
racing each other are settled by the unique constraints: the loser rolls back
and re-reads the winner's row.
"""

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import log
from app.core.supabase_auth import Claims
from app.db.models import Project, ProjectMember, Role, User

DEFAULT_PROJECT_TITLE = "My papers"


class DemoFull(Exception):
    """Account creation refused: DEMO_MAX_USERS reached."""


async def _by_subject(db: AsyncSession, sub: str) -> User | None:
    return (await db.execute(select(User).where(User.auth_subject == sub))).scalar_one_or_none()


async def _by_email(db: AsyncSession, email: str) -> User | None:
    return (
        await db.execute(select(User).where(func.lower(User.email) == email))
    ).scalar_one_or_none()


async def resolve_account(db: AsyncSession, claims: Claims) -> User:
    user = await _by_subject(db, claims.sub)
    if user is not None:
        return user

    user = await _by_email(db, claims.email)
    if user is not None:
        if user.auth_subject is not None:
            log.info("account_relinked", user_id=user.id)
        user.auth_subject = claims.sub
        await db.commit()
        return user

    if settings.demo_max_users:
        total = await db.scalar(select(func.count()).select_from(User))
        if total >= settings.demo_max_users:
            raise DemoFull

    user = User(email=claims.email, name=claims.email.split("@")[0][:120], auth_subject=claims.sub)
    db.add(user)
    try:
        await db.flush()
        project = Project(owner_id=user.id, title=DEFAULT_PROJECT_TITLE, topic_keywords=[])
        db.add(project)
        await db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=user.id, role=Role.OWNER))
        await db.commit()
    except IntegrityError:
        await db.rollback()
        winner = await _by_subject(db, claims.sub)
        if winner is None:
            raise
        return winner
    log.info("account_created", user_id=user.id)
    return user
