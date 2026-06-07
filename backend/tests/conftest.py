"""Test bootstrap.

The env overrides MUST happen before any `app.*` import: settings are read
at import time, and inside the dev container the process env points at the
real postgres (and a real LLM key). Tests run on a throwaway sqlite file and
never touch the network — every agent/LLM/search call is faked.
"""

import os
import tempfile

_TMPDIR = tempfile.mkdtemp(prefix="rx-tests-")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMPDIR}/test.db"
os.environ["ENVIRONMENT"] = "dev"
os.environ["LLM_API_KEY"] = "test-key-never-used"
os.environ["LLM_BASE_URL"] = "http://localhost:1/v1"  # unroutable: fail fast if hit

import pytest_asyncio  # noqa: E402

from app.core.security import _storage as limiter_storage  # noqa: E402
from app.db import models  # noqa: F401, E402 — register models on metadata
from app.db.base import Base  # noqa: E402
from app.db.session import engine  # noqa: E402


@pytest_asyncio.fixture(autouse=True)
async def fresh_db():
    """Empty schema + clean limiter windows per test.

    The rate limiter is in-memory and module-global: without the reset,
    API-level tests share per-IP budgets across the session and trip
    3/hour limits in unrelated tests.
    """
    limiter_storage.reset()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
