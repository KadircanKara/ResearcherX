"""Per-user limits, counted from the DB so a restart forgets nothing.

Every `enforce_*` runs BEFORE the paid action and raises `LimitExceeded`,
which `main.py` turns into a 429 carrying the fixed message. Chat turns are
counted in projects the user OWNS; papers likewise, across all of them, so a
fresh project never resets the paper cap.
"""

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import ChatConversation, ChatMessage, Paper, Project, UsageEvent, User
from app.services.usage_limits import day_start, is_over

TITLE_ASSIST = "title_assist"


class LimitExceeded(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


async def papers_owned(db: AsyncSession, user_id: str) -> int:
    q = (
        select(func.count(Paper.id))
        .join(Project, Project.id == Paper.project_id)
        .where(Project.owner_id == user_id)
    )
    return int(await db.scalar(q) or 0)


async def chat_turns_since(db: AsyncSession, since: datetime, user_id: str | None = None) -> int:
    q = select(func.count(ChatMessage.id)).where(
        ChatMessage.role == "user", ChatMessage.created_at >= since
    )
    if user_id is not None:
        q = (
            q.join(ChatConversation, ChatConversation.id == ChatMessage.conversation_id)
            .join(Project, Project.id == ChatConversation.project_id)
            .where(Project.owner_id == user_id)
        )
    return int(await db.scalar(q) or 0)


async def assists_since(db: AsyncSession, user_id: str, since: datetime) -> int:
    q = select(func.count(UsageEvent.id)).where(
        UsageEvent.user_id == user_id,
        UsageEvent.kind == TITLE_ASSIST,
        UsageEvent.created_at >= since,
    )
    return int(await db.scalar(q) or 0)


async def enforce_paper_slot(db: AsyncSession, user: User) -> None:
    limit = settings.user_max_papers
    if not limit:
        return
    # Serialise one user's concurrent creates (the upload dialog runs several
    # at once): count and insert under a row lock on the user. A no-op on
    # sqlite, which serialises writes anyway.
    await db.execute(select(User.id).where(User.id == user.id).with_for_update())
    if is_over(await papers_owned(db, user.id), limit):
        raise LimitExceeded(f"Paper limit reached ({limit}). Delete a paper to add another.")


async def enforce_chat_turn(db: AsyncSession, user: User, now: datetime | None = None) -> None:
    since = day_start(_now(now))
    total = settings.global_chat_turns_per_day
    if total and is_over(await chat_turns_since(db, since), total):
        raise LimitExceeded("The demo is at capacity for today. Try again after 00:00 UTC.")
    limit = settings.user_chat_turns_per_day
    if limit and is_over(await chat_turns_since(db, since, user.id), limit):
        raise LimitExceeded(f"Daily chat limit reached ({limit}). Resets at 00:00 UTC.")


async def enforce_title_assist(db: AsyncSession, user: User, now: datetime | None = None) -> None:
    await enforce_paper_slot(db, user)  # a full library cannot use a suggestion
    limit = settings.user_llm_assists_per_day
    if not limit:
        return
    moment = _now(now)
    if is_over(await assists_since(db, user.id, day_start(moment)), limit):
        raise LimitExceeded(f"Daily title-suggestion limit reached ({limit}). Resets at 00:00 UTC.")
    db.add(UsageEvent(user_id=user.id, kind=TITLE_ASSIST, created_at=moment))
    await db.commit()
