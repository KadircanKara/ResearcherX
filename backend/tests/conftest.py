"""Test bootstrap.

The env overrides MUST happen before any `app.*` import: settings are read
at import time, and inside the dev container the process env points at the
real postgres (and real LLM/embedding keys). Tests run on a throwaway sqlite
file and never touch the network — every agent/LLM/search/embedding call is
faked.
"""

import os
import tempfile

_TMPDIR = tempfile.mkdtemp(prefix="rx-tests-")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMPDIR}/test.db?timeout=30"
os.environ["ENVIRONMENT"] = "dev"
os.environ["LLM_API_KEY"] = "test-key-never-used"
os.environ["LLM_BASE_URL"] = "http://localhost:1/v1"  # unroutable: fail fast if hit
os.environ["EMBEDDING_API_KEY"] = "test-key-never-used"
os.environ["EMBEDDING_BASE_URL"] = "http://localhost:1/v1"  # unroutable: fail fast if hit
# Overriding the primary key is not enough: LLM_FALLBACKS carries its own
# base_url/api_key/model per entry, and pydantic-settings reads it straight
# from .env when it is not overridden here. Without this line a populated
# fallback chain puts live credentials into every test run — and pytest
# prints the whole Settings repr, keys included, in an assertion failure.
os.environ["LLM_FALLBACKS"] = "[]"

import asyncio  # noqa: E402

import pytest_asyncio  # noqa: E402

from app.api.v1.research import cancel_watchers  # noqa: E402
from app.core.security import _storage as limiter_storage  # noqa: E402
from app.db import models  # noqa: F401, E402 — register models on metadata
from app.db.base import Base  # noqa: E402
from app.db.session import engine  # noqa: E402
from app.llm.client import reset_pool  # noqa: E402
from app.services.task_registry import registry  # noqa: E402


@pytest_asyncio.fixture(autouse=True)
async def fresh_db():
    """Empty schema + clean limiter windows per test.

    The rate limiter is in-memory and module-global: without the reset,
    API-level tests share per-IP budgets across the session and trip
    3/hour limits in unrelated tests.

    cancel_all() runs in the teardown (after yield) so it executes in the
    SAME event loop that spawned the background tasks. pytest-asyncio uses
    a per-function event loop by default; cancelling from a different loop
    (setup of the next test) is a no-op and the aiosqlite file handle stays
    open, causing DROP TABLE to deadlock. Teardown-placement guarantees
    same-loop cancellation, so the next test's drop_all sees no open handles.
    """
    limiter_storage.reset()
    reset_pool()  # provider failover index is sticky module state
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    await registry.cancel_all()
    await cancel_watchers()  # grace-window watchers aren't in the registry
    # Give aiosqlite worker threads time to drain after task cancellation.
    # asyncio.gather returns once the coroutine is done, but the underlying
    # SQLite thread may still be finishing its last operation + closing the
    # file handle.  A brief sleep lets those threads exit before the next
    # test's drop_all acquires the writer lock.
    await asyncio.sleep(0.1)  # give aiosqlite threads time to release file handle


import httpx  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402


@pytest_asyncio.fixture
async def db_session():
    async with SessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def client():
    # raise_app_exceptions=False: an unhandled exception in a route still gets
    # Starlette's default 500 response (ServerErrorMiddleware sends it before
    # re-raising) — without this flag httpx re-raises the exception instead of
    # returning that response, so tests asserting on `resp.status_code` for a
    # genuine server error can never see it.
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
