from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.identity import get_current_user
from app.db.models import User
from app.db.session import get_session
from app.schemas.user import PaperUsage, TurnUsage, UsageOut, UserOut
from app.services.usage_limits import day_start, next_reset
from app.services.usage_service import chat_turns_since, papers_owned

router = APIRouter(tags=["users"])  # no prefix -> mounted under /v1


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.get("/me/usage", response_model=UsageOut)
async def my_usage(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)
) -> UsageOut:
    now = datetime.now(timezone.utc)
    return UsageOut(
        papers=PaperUsage(used=await papers_owned(db, user.id), limit=settings.user_max_papers),
        chat_turns_today=TurnUsage(
            used=await chat_turns_since(db, day_start(now), user.id),
            limit=settings.user_chat_turns_per_day,
            resets_at=next_reset(now),
        ),
    )


@router.get("/users", response_model=list[UserOut])
async def list_users(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)
) -> list[User]:
    # Real accounts are other people: listing them would hand one client every
    # other client's email. The dev seam keeps the full list for the switcher.
    if settings.auth_mode == "supabase":
        return [user]
    return list((await db.execute(select(User).order_by(User.created_at))).scalars())
