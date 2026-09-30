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


NOT_FOUND = {"detail": "Not Found"}

# (feature, method, path template, JSON body). {p} project, {d} latex document,
# {u} another user, {paper} a real paper. Bodies are valid so that a missing
# gate reaches the handler and answers something other than 404 / this body.
_GATED = [
    ("sharing", "GET", "/v1/projects/{p}/members", None),
    ("sharing", "POST", "/v1/projects/{p}/members", {"user_id": "{u}", "role": "member"}),
    ("sharing", "PATCH", "/v1/projects/{p}/members/{u}", {"role": "member"}),
    ("sharing", "DELETE", "/v1/projects/{p}/members/{u}", None),
    ("research", "GET", "/v1/projects/{p}/runs", None),
    ("research", "POST", "/v1/research", {"question": "What is RAG?", "project_id": "{p}"}),
    ("research", "GET", "/v1/research/{r}", None),
    ("research", "GET", "/v1/research/{r}/events", None),
    (
        "paper_url",
        "POST",
        "/v1/projects/{p}/papers/suggest-title-from-url",
        {"url": "https://x.org"},
    ),
    (
        "paper_url",
        "POST",
        "/v1/projects/{p}/papers/{paper}/ingest-from-url",
        {"url": "https://x.org"},
    ),
    ("latex", "GET", "/v1/projects/{p}/latex", None),
    ("latex", "GET", "/v1/projects/{p}/latex/{d}", None),
    ("latex", "GET", "/v1/projects/{p}/latex/{d}/files", None),
    ("latex", "GET", "/v1/projects/{p}/latex/{d}/members", None),
]


@pytest_asyncio.fixture
async def resources(client, project, db_session):
    """A real paper, latex document and second user, created while all is on."""
    other = (
        (await db_session.execute(select(User).where(User.email != "you@researcherx.dev")))
        .scalars()
        .first()
    )
    paper = await client.post(
        f"/v1/projects/{project.id}/papers", json={"title": "T", "source": "upload"}
    )
    doc = await client.post(f"/v1/projects/{project.id}/latex", json={"name": "d"})
    assert paper.status_code == 201 and doc.status_code == 201
    return {
        "p": project.id,
        "u": other.id,
        "paper": paper.json()["id"],
        "d": doc.json()["id"],
        "r": "00000000-0000-0000-0000-000000000000",
    }


@pytest.mark.parametrize("feature,method,path,body", _GATED)
async def test_every_gated_route_is_a_plain_404_when_its_feature_is_off(
    client, resources, off, feature, method, path, body
):
    off(feature)
    ids = resources
    json = None if body is None else {k: v.format(**ids) for k, v in body.items()}
    r = await client.request(method, path.format(**ids), json=json)
    assert r.status_code == 404
    assert r.json() == NOT_FOUND


async def test_anonymous_request_in_supabase_mode_resolves_to_none(db_session, monkeypatch):
    """The research router reads the caller through get_current_user_optional;
    with no Authorization header it must yield None, never a default user."""
    from app.core.config import settings
    from app.core.identity import get_current_user_optional

    monkeypatch.setattr(settings, "auth_mode", "supabase")
    monkeypatch.setattr(settings, "supabase_url", "https://abc.supabase.co")
    request = Request({"type": "http", "headers": [], "method": "GET", "path": "/"})
    assert await get_current_user_optional(request, db_session) is None
