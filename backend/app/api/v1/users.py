from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.identity import get_current_user
from app.db.models import User
from app.db.session import get_session
from app.schemas.user import UserOut

router = APIRouter(tags=["users"])  # no prefix -> mounted under /v1


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.get("/users", response_model=list[UserOut])
async def list_users(db: AsyncSession = Depends(get_session)) -> list[User]:
    return list((await db.execute(select(User).order_by(User.created_at))).scalars())
