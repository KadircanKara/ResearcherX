"""AUTH_MODE=supabase through the HTTP stack: bearer in, our User out."""

from unittest.mock import AsyncMock, patch

import pytest

from app.core.supabase_auth import AuthError, AuthUnavailable, Claims


@pytest.fixture
def supabase_mode(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "auth_mode", "supabase")
    monkeypatch.setattr(settings, "supabase_url", "https://abc.supabase.co")


def _verify(result):
    mock = (
        AsyncMock(side_effect=result)
        if isinstance(result, Exception)
        else AsyncMock(return_value=result)
    )
    return patch("app.core.identity.verify_token", new=mock)


async def test_no_header_is_401(client, supabase_mode):
    r = await client.get("/v1/me")
    assert r.status_code == 401 and r.json() == {"detail": "Not authenticated"}


async def test_invalid_token_is_401_with_generic_text(client, supabase_mode):
    with _verify(AuthError("ExpiredSignatureError")):
        r = await client.get("/v1/me", headers={"Authorization": "Bearer t"})
    assert r.status_code == 401 and r.json() == {"detail": "Not authenticated"}


async def test_keys_unreachable_is_503_not_401(client, supabase_mode):
    with _verify(AuthUnavailable("down")):
        r = await client.get("/v1/me", headers={"Authorization": "Bearer t"})
    assert r.status_code == 503
    assert r.json() == {"detail": "Sign-in is temporarily unavailable."}


async def test_valid_token_provisions_and_returns_me(client, supabase_mode):
    with _verify(Claims(sub="s1", email="a@x.com")):
        r = await client.get("/v1/me", headers={"Authorization": "Bearer t"})
        projects = await client.get("/v1/projects", headers={"Authorization": "Bearer t"})
    assert r.status_code == 200 and r.json()["email"] == "a@x.com"
    assert [p["title"] for p in projects.json()] == ["My papers"]


async def test_dev_header_is_ignored_in_supabase_mode(client, supabase_mode):
    r = await client.get("/v1/me", headers={"X-Dev-User-Id": "anything"})
    assert r.status_code == 401


async def test_users_list_is_only_the_caller(client, supabase_mode, db_session):
    from app.db.models import User

    db_session.add(User(email="other@x.com", name="Other"))
    await db_session.commit()
    with _verify(Claims(sub="s1", email="a@x.com")):
        r = await client.get("/v1/users", headers={"Authorization": "Bearer t"})
    assert [u["email"] for u in r.json()] == ["a@x.com"]


async def test_demo_full_is_403(client, supabase_mode, monkeypatch, db_session):
    from app.core.config import settings
    from app.db.models import User

    monkeypatch.setattr(settings, "demo_max_users", 1)
    db_session.add(User(email="first@x.com", name="First"))
    await db_session.commit()
    with _verify(Claims(sub="s2", email="b@x.com")):
        r = await client.get("/v1/me", headers={"Authorization": "Bearer t"})
    assert r.status_code == 403 and r.json() == {"detail": "The demo is full."}


async def test_seed_dev_data_is_skipped_in_supabase_mode(db_session, supabase_mode):
    from sqlalchemy import func, select

    from app.db.models import User
    from app.db.seed import seed_dev_data

    await seed_dev_data(db_session)
    assert await db_session.scalar(select(func.count()).select_from(User)) == 0
