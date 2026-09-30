from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.accounts import DemoFull, resolve_account
from app.core.config import settings
from app.core.logging import log
from app.core.supabase_auth import AuthError, AuthUnavailable, jwks_cache, verify_token
from app.db.models import User
from app.db.session import get_session

DEFAULT_USER_EMAIL = "you@researcherx.dev"


async def _supabase_user(request: Request, db: AsyncSession) -> User:
    """Bearer token -> verified claims -> our User. Reasons stay in the log."""
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        claims = await verify_token(token, jwks_cache())
    except AuthError as exc:
        log.info("auth_rejected", reason=str(exc))
        raise HTTPException(status_code=401, detail="Not authenticated") from None
    except AuthUnavailable:
        log.error("auth_keys_unavailable")
        raise HTTPException(status_code=503, detail="Sign-in is temporarily unavailable.") from None
    try:
        return await resolve_account(db, claims)
    except DemoFull:
        raise HTTPException(status_code=403, detail="The demo is full.") from None


async def get_current_user_optional(
    request: Request, db: AsyncSession = Depends(get_session)
) -> User | None:
    """Resolve identity only when an explicit identity header is present.

    Returns None when no identity is provided — callers decide whether to
    require it. Does NOT fall back to the default dev user; that fallback
    is only in get_current_user.
    """
    if settings.auth_mode == "supabase":
        if not request.headers.get("Authorization"):
            return None
        return await _supabase_user(request, db)
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
    AUTH_MODE=supabase replaces the dev seam with a verified Supabase token (see
    `_supabase_user`); callers and signature are unchanged.
    """
    if settings.auth_mode == "supabase":
        return await _supabase_user(request, db)
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
