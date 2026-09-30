from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.identity import get_current_user
from app.db.models import User
from app.db.session import get_session
from app.schemas.user import UserOut

router = APIRouter(tags=["users"])  # no prefix -> mounted under /v1


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.get("/users", response_model=list[UserOut])
async def list_users(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)
) -> list[User]:
    # Real accounts are other people: listing them would hand one client every
    # other client's email. The dev seam keeps the full list for the switcher.
    if settings.auth_mode == "supabase":
        return [user]
    return list((await db.execute(select(User).order_by(User.created_at))).scalars())
