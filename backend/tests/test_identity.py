from sqlalchemy import select
import pytest_asyncio
from app.db.models import User
from app.db.seed import seed_users
from app.core.identity import DEFAULT_USER_EMAIL


@pytest_asyncio.fixture
async def seeded(db_session):
    await seed_users(db_session)
    await db_session.commit()


async def test_me_defaults_to_you(client, seeded):
    r = await client.get("/v1/me")
    assert r.status_code == 200
    assert r.json()["email"] == DEFAULT_USER_EMAIL


async def test_dev_header_switches_principal(client, seeded, db_session):
    amelia = (await db_session.execute(select(User).where(User.email == "amelia@lab.io"))).scalar_one()
    r = await client.get("/v1/me", headers={"X-Dev-User-Id": amelia.id})
    assert r.json()["email"] == "amelia@lab.io"


async def test_dev_header_ignored_outside_dev(client, seeded, db_session, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "environment", "prod")
    amelia = (await db_session.execute(select(User).where(User.email == "amelia@lab.io"))).scalar_one()
    r = await client.get("/v1/me", headers={"X-Dev-User-Id": amelia.id})
    assert r.json()["email"] == DEFAULT_USER_EMAIL   # header ignored


async def test_list_users_returns_seeds(client, seeded):
    r = await client.get("/v1/users")
    assert r.status_code == 200
    assert {"you@researcherx.dev", "amelia@lab.io", "marco@lab.io"} <= {u["email"] for u in r.json()}
