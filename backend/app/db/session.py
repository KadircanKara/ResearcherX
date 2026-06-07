from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings

# Pool bounds only apply to a real server DB. sqlite (tests, keyless boot
# fallback) gets NullPool instead: pooled aiosqlite connections are bound to
# the event loop that created them, which breaks under pytest's
# loop-per-test model.
_pool_kwargs: dict[str, Any] = {"poolclass": NullPool}
if settings.database_url.startswith("postgresql"):
    _pool_kwargs = {
        # Single worker, max_parallel_searchers=3 short-lived sessions plus
        # API requests — 5+5 is generous headroom, not a real ceiling.
        "pool_size": 5,
        "max_overflow": 5,
        # Detect connections silently killed (db restart, idle timeout)
        # instead of failing the first query after.
        "pool_pre_ping": True,
        "pool_recycle": 1800,
    }

engine = create_async_engine(settings.database_url, echo=False, future=True, **_pool_kwargs)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
