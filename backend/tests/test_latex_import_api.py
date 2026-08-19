"""Importing a project archive. The route is the only place hostile bytes
enter the system, so every rejection is asserted to leave NOTHING behind."""

import io
import zipfile

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LatexDocument, Project, ProjectMember, User
from app.db.seed import seed_users

DOC = b"\\documentclass{article}\n\\begin{document}\nHi\n\\end{document}"


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


@pytest_asyncio.fixture
async def you(db_session: AsyncSession) -> User:
    await seed_users(db_session)
    await db_session.commit()
    return (
        await db_session.execute(select(User).where(User.email == "you@researcherx.dev"))
    ).scalar_one()


@pytest_asyncio.fixture
async def project(db_session: AsyncSession, you: User) -> Project:
    p = Project(owner_id=you.id, title="Import", topic_keywords=[])
    db_session.add(p)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=p.id, user_id=you.id, role="owner"))
    await db_session.commit()
    await db_session.refresh(p)
    return p


def _h(user: User) -> dict:
    return {"X-Dev-User-Id": user.id, "Content-Type": "application/zip"}


async def test_importing_a_project_creates_a_document_with_its_tree(
    client: AsyncClient, you: User, project: Project
):
    blob = _zip({"main.tex": DOC, "chapters/intro.tex": b"\\section{I}", "f.png": b"\x89PNG\x00"})
    resp = await client.post(
        f"/v1/projects/{project.id}/latex/import", content=blob, headers=_h(you)
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["main_path"] == "main.tex"

    tree = await client.get(
        f"/v1/projects/{project.id}/latex/{body['id']}/files",
        headers={"X-Dev-User-Id": you.id},
    )
    assert sorted(f["path"] for f in tree.json()["files"]) == [
        "chapters/intro.tex",
        "f.png",
        "main.tex",
    ]
    assert [f["is_binary"] for f in tree.json()["files"] if f["path"] == "f.png"] == [True]


async def test_an_imported_project_reports_its_source_through_the_shim(
    client: AsyncClient, you: User, project: Project
):
    blob = _zip({"main.tex": DOC})
    created = await client.post(
        f"/v1/projects/{project.id}/latex/import", content=blob, headers=_h(you)
    )
    got = await client.get(
        f"/v1/projects/{project.id}/latex/{created.json()['id']}",
        headers={"X-Dev-User-Id": you.id},
    )
    assert got.json()["source"] == DOC.decode()


async def test_a_fontspec_project_is_created_as_xelatex(
    client: AsyncClient, you: User, project: Project
):
    src = b"\\documentclass{article}\n\\usepackage{fontspec}\n\\begin{document}\\end{document}"
    resp = await client.post(
        f"/v1/projects/{project.id}/latex/import", content=_zip({"main.tex": src}), headers=_h(you)
    )
    assert resp.json()["engine"] == "xelatex"


async def test_an_ambiguous_main_file_is_a_422_listing_the_candidates(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    blob = _zip({"a.tex": DOC, "b.tex": DOC})
    resp = await client.post(
        f"/v1/projects/{project.id}/latex/import", content=blob, headers=_h(you)
    )
    assert resp.status_code == 422
    assert sorted(resp.json()["detail"]["candidates"]) == ["a.tex", "b.tex"]
    assert (await db_session.execute(select(LatexDocument))).scalars().all() == []


async def test_reposting_with_an_explicit_main_path_resolves_the_ambiguity(
    client: AsyncClient, you: User, project: Project
):
    blob = _zip({"a.tex": DOC, "b.tex": DOC})
    resp = await client.post(
        f"/v1/projects/{project.id}/latex/import?main_path=b.tex", content=blob, headers=_h(you)
    )
    assert resp.status_code == 201
    assert resp.json()["main_path"] == "b.tex"


async def test_an_explicit_main_path_not_in_the_archive_is_a_422(
    client: AsyncClient, you: User, project: Project
):
    resp = await client.post(
        f"/v1/projects/{project.id}/latex/import?main_path=ghost.tex",
        content=_zip({"main.tex": DOC}),
        headers=_h(you),
    )
    assert resp.status_code == 422


async def test_an_explicit_main_path_pointing_at_a_binary_is_a_422(
    client: AsyncClient, you: User, project: Project
):
    resp = await client.post(
        f"/v1/projects/{project.id}/latex/import?main_path=f.png",
        content=_zip({"main.tex": DOC, "f.png": b"\x89PNG\x00"}),
        headers=_h(you),
    )
    assert resp.status_code == 422


async def test_an_explicit_main_path_pointing_at_a_non_tex_file_is_a_422(
    client: AsyncClient, you: User, project: Project
):
    resp = await client.post(
        f"/v1/projects/{project.id}/latex/import?main_path=notes.txt",
        content=_zip({"main.tex": DOC, "notes.txt": b"hello"}),
        headers=_h(you),
    )
    assert resp.status_code == 422


async def test_an_archive_with_no_main_file_is_a_422_and_creates_nothing(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    resp = await client.post(
        f"/v1/projects/{project.id}/latex/import",
        content=_zip({"notes.txt": b"hello"}),
        headers=_h(you),
    )
    assert resp.status_code == 422
    assert (await db_session.execute(select(LatexDocument))).scalars().all() == []


async def test_a_traversal_entry_is_a_422_naming_it_and_creates_nothing(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    blob = _zip({"main.tex": DOC, "../../etc/passwd": b"pwned"})
    resp = await client.post(
        f"/v1/projects/{project.id}/latex/import", content=blob, headers=_h(you)
    )
    assert resp.status_code == 422
    assert "passwd" in str(resp.json()["detail"])
    assert (await db_session.execute(select(LatexDocument))).scalars().all() == []


async def test_a_corrupt_archive_is_a_422(client: AsyncClient, you: User, project: Project):
    resp = await client.post(
        f"/v1/projects/{project.id}/latex/import", content=b"not a zip", headers=_h(you)
    )
    assert resp.status_code == 422


async def test_an_archive_that_expands_past_the_cap_is_a_413_and_creates_nothing(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession, monkeypatch
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "latex_project_max_bytes", 1024)
    blob = _zip({"main.tex": DOC, "big.tex": b"A" * 8192})
    resp = await client.post(
        f"/v1/projects/{project.id}/latex/import", content=blob, headers=_h(you)
    )
    assert resp.status_code == 413
    assert (await db_session.execute(select(LatexDocument))).scalars().all() == []


async def test_an_upload_body_over_the_cap_is_a_413_before_it_is_all_buffered(
    client: AsyncClient, you: User, project: Project, monkeypatch
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "latex_project_max_bytes", 64)
    pulled = 0

    async def body():
        nonlocal pulled
        for _ in range(64):
            pulled += 1
            yield b"x" * 32

    resp = await client.post(
        f"/v1/projects/{project.id}/latex/import", content=body(), headers=_h(you)
    )
    assert resp.status_code == 413
    assert pulled < 64  # the server stopped consuming before the end


async def test_a_chunked_upload_with_no_content_length_is_still_capped(
    client: AsyncClient, you: User, project: Project, monkeypatch
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "latex_project_max_bytes", 64)
    pulled = 0

    async def body():
        nonlocal pulled
        for _ in range(16):
            pulled += 1
            yield b"x" * 32

    resp = await client.post(
        f"/v1/projects/{project.id}/latex/import", content=body(), headers=_h(you)
    )
    assert resp.status_code == 413
    assert pulled < 16  # the server stopped consuming before the end


async def test_a_viewer_cannot_import(
    client: AsyncClient, db_session: AsyncSession, project: Project, you: User
):
    viewer = (
        await db_session.execute(select(User).where(User.email == "amelia@lab.io"))
    ).scalar_one()
    db_session.add(ProjectMember(project_id=project.id, user_id=viewer.id, role="viewer"))
    await db_session.commit()

    resp = await client.post(
        f"/v1/projects/{project.id}/latex/import",
        content=_zip({"main.tex": DOC}),
        headers=_h(viewer),
    )
    assert resp.status_code == 403


async def test_a_non_member_gets_404_on_import(
    client: AsyncClient, db_session: AsyncSession, project: Project
):
    stranger = (
        await db_session.execute(select(User).where(User.email == "marco@lab.io"))
    ).scalar_one()
    resp = await client.post(
        f"/v1/projects/{project.id}/latex/import",
        content=_zip({"main.tex": DOC}),
        headers=_h(stranger),
    )
    assert resp.status_code == 404


async def test_import_never_overwrites_an_existing_document(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    for _ in range(2):
        await client.post(
            f"/v1/projects/{project.id}/latex/import",
            content=_zip({"main.tex": DOC}),
            headers=_h(you),
        )
    rows = (await db_session.execute(select(LatexDocument))).scalars().all()
    assert len(rows) == 2


async def test_an_over_long_name_is_a_422(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    resp = await client.post(
        f"/v1/projects/{project.id}/latex/import?name={'x' * 201}",
        content=_zip({"main.tex": DOC}),
        headers=_h(you),
    )
    assert resp.status_code == 422
    assert (await db_session.execute(select(LatexDocument))).scalars().all() == []


async def test_exporting_returns_every_file_in_the_tree(
    client: AsyncClient, you: User, project: Project
):
    blob = _zip({"main.tex": DOC, "chapters/intro.tex": b"\\section{I}", "f.png": b"\x89PNG\x00"})
    created = await client.post(
        f"/v1/projects/{project.id}/latex/import", content=blob, headers=_h(you)
    )
    doc_id = created.json()["id"]

    resp = await client.get(
        f"/v1/projects/{project.id}/latex/{doc_id}/export",
        headers={"X-Dev-User-Id": you.id},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"

    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
        assert sorted(z.namelist()) == ["chapters/intro.tex", "f.png", "main.tex"]
        assert z.read("main.tex") == DOC
        assert z.read("f.png") == b"\x89PNG\x00"


async def test_an_exported_archive_reimports_to_an_identical_tree(
    client: AsyncClient, you: User, project: Project
):
    """The round trip is the point: a project you can upload but never get
    back out is a roach motel."""
    original = _zip({"main.tex": DOC, "chapters/intro.tex": b"\\section{I}"})
    first = await client.post(
        f"/v1/projects/{project.id}/latex/import", content=original, headers=_h(you)
    )
    exported = await client.get(
        f"/v1/projects/{project.id}/latex/{first.json()['id']}/export",
        headers={"X-Dev-User-Id": you.id},
    )
    second = await client.post(
        f"/v1/projects/{project.id}/latex/import", content=exported.content, headers=_h(you)
    )
    assert second.status_code == 201
    assert second.json()["main_path"] == first.json()["main_path"]
    assert second.json()["file_count"] == first.json()["file_count"]


async def test_a_non_member_gets_404_on_export(
    client: AsyncClient, db_session: AsyncSession, project: Project, you: User
):
    created = await client.post(
        f"/v1/projects/{project.id}/latex/import",
        content=_zip({"main.tex": DOC}),
        headers=_h(you),
    )
    stranger = (
        await db_session.execute(select(User).where(User.email == "marco@lab.io"))
    ).scalar_one()

    resp = await client.get(
        f"/v1/projects/{project.id}/latex/{created.json()['id']}/export",
        headers={"X-Dev-User-Id": stranger.id},
    )
    assert resp.status_code == 404
