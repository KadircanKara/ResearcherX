"""First-login provisioning: one user and one project per Supabase subject."""

import pytest
from sqlalchemy import func, select

from app.core.accounts import DEFAULT_PROJECT_TITLE, DemoFull, resolve_account
from app.core.supabase_auth import Claims
from app.db.models import Project, ProjectMember, User


async def _count(db, model):
    return await db.scalar(select(func.count()).select_from(model))


async def test_first_login_creates_user_and_one_owned_project(db_session):
    user = await resolve_account(db_session, Claims(sub="s1", email="a@x.com"))
    assert user.auth_subject == "s1" and user.email == "a@x.com"
    projects = (await db_session.execute(select(Project))).scalars().all()
    assert [p.title for p in projects] == [DEFAULT_PROJECT_TITLE]
    assert projects[0].owner_id == user.id
    member = (await db_session.execute(select(ProjectMember))).scalar_one()
    assert (member.user_id, member.role) == (user.id, "owner")


async def test_second_login_returns_the_same_user_and_creates_nothing(db_session):
    first = await resolve_account(db_session, Claims(sub="s1", email="a@x.com"))
    again = await resolve_account(db_session, Claims(sub="s1", email="a@x.com"))
    assert again.id == first.id
    assert await _count(db_session, User) == 1
    assert await _count(db_session, Project) == 1


async def test_email_match_is_case_insensitive(db_session):
    seeded = User(email="Client@Acme.com", name="Client")
    db_session.add(seeded)
    await db_session.commit()
    seeded_id = seeded.id
    user = await resolve_account(db_session, Claims(sub="s9", email="client@acme.com"))
    assert user.id == seeded_id
    assert user.email == "Client@Acme.com" and user.auth_subject == "s9"
    assert await _count(db_session, User) == 1


async def test_a_reinvited_client_is_relinked_and_keeps_their_data(db_session):
    old = await resolve_account(db_session, Claims(sub="old-sub", email="a@x.com"))
    again = await resolve_account(db_session, Claims(sub="new-sub", email="a@x.com"))
    assert again.id == old.id and again.auth_subject == "new-sub"
    assert await _count(db_session, Project) == 1


async def test_creation_is_refused_at_the_user_cap(db_session, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "demo_max_users", 1)
    await resolve_account(db_session, Claims(sub="s1", email="a@x.com"))
    with pytest.raises(DemoFull):
        await resolve_account(db_session, Claims(sub="s2", email="b@x.com"))
    # an existing account still resolves at the cap
    assert (await resolve_account(db_session, Claims(sub="s1", email="a@x.com"))).email == "a@x.com"


async def test_a_lost_race_on_the_subject_returns_the_winner(db_session, monkeypatch):
    from app.core import accounts

    winner = User(email="a@x.com", name="a", auth_subject="s1")
    db_session.add(winner)
    await db_session.commit()
    winner_id = winner.id

    real = accounts._by_subject
    calls = {"n": 0}

    async def stale_first(db, sub):
        calls["n"] += 1
        return None if calls["n"] == 1 else await real(db, sub)

    async def no_email(db, email):
        return None

    monkeypatch.setattr(accounts, "_by_subject", stale_first)
    monkeypatch.setattr(accounts, "_by_email", no_email)
    user = await resolve_account(db_session, Claims(sub="s1", email="a@x.com"))
    assert user.id == winner_id
    assert await _count(db_session, User) == 1
    assert await _count(db_session, Project) == 0


async def test_a_lost_race_on_the_email_relinks_to_the_winner(db_session, monkeypatch):
    from app.core import accounts

    winner = User(email="a@x.com", name="a", auth_subject="other")
    db_session.add(winner)
    await db_session.commit()
    winner_id = winner.id

    real = accounts._by_email
    calls = {"n": 0}

    async def stale_first(db, email):
        calls["n"] += 1
        return None if calls["n"] == 1 else await real(db, email)

    monkeypatch.setattr(accounts, "_by_email", stale_first)
    user = await resolve_account(db_session, Claims(sub="mine", email="a@x.com"))
    assert user.id == winner_id and user.auth_subject == "mine"
    assert await _count(db_session, User) == 1
