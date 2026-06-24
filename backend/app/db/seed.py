from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User

SEED_USERS = [
    {"email": "you@researcherx.dev", "name": "You", "avatar_color": "#2D3FE0"},
    {"email": "amelia@lab.io", "name": "Amelia Chen", "avatar_color": "#E0457F"},
    {"email": "marco@lab.io", "name": "Marco Rossi", "avatar_color": "#1FAE6B"},
]


async def seed_users(db: AsyncSession) -> None:
    """Create the demo users if absent. Idempotent (keyed on email)."""
    existing = {e for (e,) in await db.execute(select(User.email))}
    for spec in SEED_USERS:
        if spec["email"] not in existing:
            db.add(User(**spec))
    await db.flush()
