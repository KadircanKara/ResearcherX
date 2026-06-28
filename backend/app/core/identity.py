from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import log
from app.db.models import User
from app.db.session import get_session

DEFAULT_USER_EMAIL = "you@researcherx.dev"


async def get_current_user_optional(
    request: Request, db: AsyncSession = Depends(get_session)
) -> User | None:
    """Resolve identity only when an explicit identity header is present.

    Returns None when no identity is provided — callers decide whether to
    require it. Does NOT fall back to the default dev user; that fallback
    is only in get_current_user.
    """
    if settings.environment == "dev":
        dev_id = request.headers.get("X-Dev-User-Id")
        if dev_id:
            user = await db.get(User, dev_id)
            if user is not None:
                return user
    return None


async def get_current_user(request: Request, db: AsyncSession = Depends(get_session)) -> User:
    """Resolve the acting principal. Auth is deferred.

    In dev, an X-Dev-User-Id header lets the client act as a seeded teammate so
    collaboration features are exercisable without login. The auth phase replaces
    ONLY this body (validate a JWT cookie); callers and signature are unchanged.
    """
    if settings.environment == "dev":
        dev_id = request.headers.get("X-Dev-User-Id")
        if dev_id:
            user = await db.get(User, dev_id)
            if user is not None:
                return user
    user = (
        await db.execute(select(User).where(User.email == DEFAULT_USER_EMAIL))
    ).scalar_one_or_none()
    if user is None:
        log.error("identity_seed_missing")
        raise HTTPException(status_code=500, detail="internal server error")
    return user
