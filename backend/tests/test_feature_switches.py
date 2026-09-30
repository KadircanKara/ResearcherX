"""Switched-off features answer exactly like routes that do not exist."""

import pytest
import pytest_asyncio
from sqlalchemy import select
from starlette.requests import Request

from app.db.models import Project, ProjectMember, User
from app.db.seed import seed_users


@pytest_asyncio.fixture
async def project(db_session):
    await seed_users(db_session)
    await db_session.commit()
    you = (
        await db_session.execute(select(User).where(User.email == "you@researcherx.dev"))
    ).scalar_one()
    p = Project(owner_id=you.id, title="P", topic_keywords=[])
    db_session.add(p)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=p.id, user_id=you.id, role="owner"))
    await db_session.commit()
    return p


@pytest.fixture
def off(monkeypatch):
    from app.core.config import settings

    def _off(name):
        monkeypatch.setattr(settings, f"feature_{name}", False)

    return _off


async def test_latex_off_is_404(client, project, off):
    off("latex")
    assert (await client.get(f"/v1/projects/{project.id}/latex")).status_code == 404


async def test_research_off_is_404(client, project, off):
    off("research")
    assert (await client.get("/v1/research/some-id")).status_code == 404
    assert (await client.get(f"/v1/projects/{project.id}/runs")).status_code == 404


async def test_sharing_off_is_404(client, project, off):
    off("sharing")
    assert (await client.get(f"/v1/projects/{project.id}/members")).status_code == 404


async def test_paper_url_off_is_404_and_refuses_link_papers(client, project, off):
    off("paper_url")
    r = await client.post(
        f"/v1/projects/{project.id}/papers/suggest-title-from-url", json={"url": "https://x.org"}
    )
    assert r.status_code == 404
    r = await client.post(
        f"/v1/projects/{project.id}/papers", json={"title": "T", "source": "link"}
    )
    assert r.status_code == 422


async def test_manual_off_refuses_manual_but_allows_upload(client, project, off):
    off("manual_papers")
    r = await client.post(f"/v1/projects/{project.id}/papers", json={"title": "T"})
    assert r.status_code == 422  # source defaults to manual
    r = await client.post(
        f"/v1/projects/{project.id}/papers", json={"title": "T", "source": "upload"}
    )
    assert r.status_code == 201


async def test_everything_on_by_default(client, project):
    assert (await client.get(f"/v1/projects/{project.id}/members")).status_code == 200
    assert (await client.get(f"/v1/projects/{project.id}/latex")).status_code == 200


async def test_anonymous_request_in_supabase_mode_resolves_to_none(db_session, monkeypatch):
    """The research router reads the caller through get_current_user_optional;
    with no Authorization header it must yield None, never a default user."""
    from app.core.config import settings
    from app.core.identity import get_current_user_optional

    monkeypatch.setattr(settings, "auth_mode", "supabase")
    monkeypatch.setattr(settings, "supabase_url", "https://abc.supabase.co")
    request = Request({"type": "http", "headers": [], "method": "GET", "path": "/"})
    assert await get_current_user_optional(request, db_session) is None
