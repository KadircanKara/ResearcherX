"""Unhandled server errors must still be readable by the browser.

An exception that escapes a route is turned into a 500 by Starlette's
ServerErrorMiddleware, which sits OUTSIDE CORSMiddleware — so that response
ships without `access-control-allow-origin` and the browser discards it,
surfacing as an opaque `TypeError: Failed to fetch` in the client. Every
unhandled backend error is invisible to the frontend unless the 500 is built
inside the CORS layer.
"""

from unittest.mock import AsyncMock, patch

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.seed import seed_users

_ORIGIN = "http://localhost:3000"


@pytest_asyncio.fixture
async def seeded(db_session: AsyncSession):
    await seed_users(db_session)
    await db_session.commit()


async def test_unhandled_route_error_returns_500_with_cors_headers(client: AsyncClient, seeded):
    with patch(
        "app.api.v1.projects.project_service.list_projects",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        resp = await client.get("/v1/projects", headers={"Origin": _ORIGIN})

    assert resp.status_code == 500
    assert resp.headers.get("access-control-allow-origin") == _ORIGIN


async def test_unhandled_route_error_body_is_generic(client: AsyncClient, seeded):
    """The client-visible body must not leak the exception text."""
    with patch(
        "app.api.v1.projects.project_service.list_projects",
        new=AsyncMock(side_effect=RuntimeError("boom: secret internal detail")),
    ):
        resp = await client.get("/v1/projects", headers={"Origin": _ORIGIN})

    assert resp.status_code == 500
    assert "secret internal detail" not in resp.text
    assert resp.json()["detail"] == "internal server error"
