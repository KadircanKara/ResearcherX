"""Importing a project archive. The route is the only place hostile bytes
enter the system, so every rejection is asserted to leave NOTHING behind."""

import io
import json
import zipfile

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LatexDocument, Project, ProjectMember, User
from app.db.seed import seed_users
from app.services.latex_paths import MANIFEST_PATH
from app.services.latex_staging import staging

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


async def _import(
    client: AsyncClient,
    user: User,
    project_id: str,
    blob,
    *,
    name: str | None = None,
    main_path: str | None = None,
    document_id: str | None = None,
):
    """The two-step import driven as one call.

    Import is `plan` (upload the archive, learn what the user must decide)
    then `commit` (redeem the token, write the tree). Most tests in this
    file are about IMPORTING -- what lands in the tree, what is refused,
    what round-trips -- not about the flow, so they go through this helper.
    The tests that are about the flow itself drive the two routes directly.

    A plan that refuses is the import refusing, so its response is returned
    as-is: an archive the plan step rejects never reaches a commit.
    """
    params: dict[str, str] = {}
    if name is not None:
        params["name"] = name
    if main_path is not None:
        params["main_path"] = main_path
    if document_id is not None:
        params["document_id"] = document_id

    plan = await client.post(
        f"/v1/projects/{project_id}/latex/import/plan",
        params=params,
        content=blob,
        headers=_h(user),
    )
    if plan.status_code != 200:
        return plan

    body: dict = {"staging_id": plan.json()["staging_id"]}
    if name is not None:
        body["name"] = name
    if main_path is not None:
        body["main_path"] = main_path
    if document_id is not None:
        body["document_id"] = document_id
    return await client.post(
        f"/v1/projects/{project_id}/latex/import/commit",
        json=body,
        headers={"X-Dev-User-Id": user.id},
    )


async def test_importing_a_project_creates_a_document_with_its_tree(
    client: AsyncClient, you: User, project: Project
):
    blob = _zip({"main.tex": DOC, "chapters/intro.tex": b"\\section{I}", "f.png": b"\x89PNG\x00"})
    resp = await _import(client, you, project.id, blob)
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


async def test_an_imported_projects_main_file_content_is_readable(
    client: AsyncClient, you: User, project: Project
):
    blob = _zip({"main.tex": DOC})
    created = await _import(client, you, project.id, blob)
    doc_id = created.json()["id"]
    assert "source" not in created.json()

    file_read = await client.get(
        f"/v1/projects/{project.id}/latex/{doc_id}/file",
        params={"path": "main.tex"},
        headers={"X-Dev-User-Id": you.id},
    )
    assert file_read.json()["content"] == DOC.decode()


async def test_a_fontspec_project_is_created_as_xelatex(
    client: AsyncClient, you: User, project: Project
):
    src = b"\\documentclass{article}\n\\usepackage{fontspec}\n\\begin{document}\\end{document}"
    resp = await _import(client, you, project.id, _zip({"main.tex": src}))
    assert resp.json()["engine"] == "xelatex"


async def test_an_ambiguous_main_file_is_a_422_listing_the_candidates(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    blob = _zip({"a.tex": DOC, "b.tex": DOC})
    resp = await _import(client, you, project.id, blob)
    assert resp.status_code == 422
    assert sorted(resp.json()["detail"]["candidates"]) == ["a.tex", "b.tex"]
    assert (await db_session.execute(select(LatexDocument))).scalars().all() == []


async def test_reposting_with_an_explicit_main_path_resolves_the_ambiguity(
    client: AsyncClient, you: User, project: Project
):
    blob = _zip({"a.tex": DOC, "b.tex": DOC})
    resp = await _import(client, you, project.id, blob, main_path="b.tex")
    assert resp.status_code == 201
    assert resp.json()["main_path"] == "b.tex"


async def test_an_explicit_main_path_not_in_the_archive_is_a_422(
    client: AsyncClient, you: User, project: Project
):
    resp = await _import(client, you, project.id, _zip({"main.tex": DOC}), main_path="ghost.tex")
    assert resp.status_code == 422


async def test_an_explicit_main_path_pointing_at_a_binary_is_a_422(
    client: AsyncClient, you: User, project: Project
):
    resp = await _import(
        client, you, project.id, _zip({"main.tex": DOC, "f.png": b"\x89PNG\x00"}), main_path="f.png"
    )
    assert resp.status_code == 422


async def test_an_explicit_main_path_pointing_at_a_non_tex_file_is_a_422(
    client: AsyncClient, you: User, project: Project
):
    resp = await _import(
        client,
        you,
        project.id,
        _zip({"main.tex": DOC, "notes.txt": b"hello"}),
        main_path="notes.txt",
    )
    assert resp.status_code == 422


async def test_an_archive_with_no_main_file_is_a_422_and_creates_nothing(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    resp = await _import(client, you, project.id, _zip({"notes.txt": b"hello"}))
    assert resp.status_code == 422
    assert (await db_session.execute(select(LatexDocument))).scalars().all() == []


async def test_a_traversal_entry_is_a_422_naming_it_and_creates_nothing(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    blob = _zip({"main.tex": DOC, "../../etc/passwd": b"pwned"})
    resp = await _import(client, you, project.id, blob)
    assert resp.status_code == 422
    assert "passwd" in str(resp.json()["detail"])
    assert (await db_session.execute(select(LatexDocument))).scalars().all() == []


async def test_a_corrupt_archive_is_a_422(client: AsyncClient, you: User, project: Project):
    resp = await _import(client, you, project.id, b"not a zip")
    assert resp.status_code == 422


async def test_an_archive_that_expands_past_the_cap_is_a_413_and_creates_nothing(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession, monkeypatch
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "latex_project_max_bytes", 1024)
    blob = _zip({"main.tex": DOC, "big.tex": b"A" * 8192})
    resp = await _import(client, you, project.id, blob)
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

    resp = await _import(client, you, project.id, body())
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

    resp = await _import(client, you, project.id, body())
    assert resp.status_code == 413
    assert pulled < 16  # the server stopped consuming before the end


async def test_any_member_may_import(
    client: AsyncClient, db_session: AsyncSession, project: Project, you: User
):
    """Import creates a NEW document and is project-scoped -- any project
    member may start one (`created_by` then makes them its editor), the same
    rule `create_document` follows. Project membership is binary
    (owner/member) now; there is no project-level "viewer" that could be
    refused here."""
    member = (
        await db_session.execute(select(User).where(User.email == "amelia@lab.io"))
    ).scalar_one()
    db_session.add(ProjectMember(project_id=project.id, user_id=member.id, role="member"))
    await db_session.commit()

    resp = await _import(client, member, project.id, _zip({"main.tex": DOC}))
    assert resp.status_code == 201


async def test_a_non_member_gets_404_on_import(
    client: AsyncClient, db_session: AsyncSession, project: Project
):
    stranger = (
        await db_session.execute(select(User).where(User.email == "marco@lab.io"))
    ).scalar_one()
    resp = await _import(client, stranger, project.id, _zip({"main.tex": DOC}))
    assert resp.status_code == 404


async def test_import_never_overwrites_an_existing_document(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    for _ in range(2):
        await _import(client, you, project.id, _zip({"main.tex": DOC}))
    rows = (await db_session.execute(select(LatexDocument))).scalars().all()
    assert len(rows) == 2


async def test_an_over_long_name_is_a_422(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    resp = await _import(client, you, project.id, _zip({"main.tex": DOC}), name=f"{'x' * 201}")
    assert resp.status_code == 422
    assert (await db_session.execute(select(LatexDocument))).scalars().all() == []


async def test_exporting_returns_every_file_in_the_tree(
    client: AsyncClient, you: User, project: Project
):
    blob = _zip({"main.tex": DOC, "chapters/intro.tex": b"\\section{I}", "f.png": b"\x89PNG\x00"})
    created = await _import(client, you, project.id, blob)
    doc_id = created.json()["id"]

    resp = await client.get(
        f"/v1/projects/{project.id}/latex/{doc_id}/export",
        headers={"X-Dev-User-Id": you.id},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"

    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
        assert sorted(z.namelist()) == [
            ".researcherx.json",
            "chapters/intro.tex",
            "f.png",
            "main.tex",
        ]
        assert z.read("main.tex") == DOC
        assert z.read("f.png") == b"\x89PNG\x00"


async def test_an_exported_archive_reimports_to_an_identical_tree(
    client: AsyncClient, you: User, project: Project
):
    """The round trip is the point: a project you can upload but never get
    back out is a roach motel.

    Asserts the FULL tree -- paths, is_binary, size_bytes -- and reads back
    one chapter's bytes, not just main_path/file_count. Those two alone stay
    green under a mutation that empties every non-main text file on export
    (verified live: apply the mutation below, this test goes red; revert, it
    goes green)::

        archive.writestr(full.path, full.blob if full.is_binary else "")
    """
    intro = b"\\section{I}\nSome body text that must survive the round trip."
    original = _zip({"main.tex": DOC, "chapters/intro.tex": intro})
    first = await _import(client, you, project.id, original)
    exported = await client.get(
        f"/v1/projects/{project.id}/latex/{first.json()['id']}/export",
        headers={"X-Dev-User-Id": you.id},
    )
    second = await _import(client, you, project.id, exported.content)
    assert second.status_code == 201
    assert second.json()["main_path"] == first.json()["main_path"]
    assert second.json()["file_count"] == first.json()["file_count"]

    first_tree = await client.get(
        f"/v1/projects/{project.id}/latex/{first.json()['id']}/files",
        headers={"X-Dev-User-Id": you.id},
    )
    second_tree = await client.get(
        f"/v1/projects/{project.id}/latex/{second.json()['id']}/files",
        headers={"X-Dev-User-Id": you.id},
    )

    def _shape(payload: dict) -> list[tuple[str, bool, int]]:
        return sorted((f["path"], f["is_binary"], f["size_bytes"]) for f in payload["files"])

    assert _shape(first_tree.json()) == _shape(second_tree.json())

    second_intro = await client.get(
        f"/v1/projects/{project.id}/latex/{second.json()['id']}/file",
        params={"path": "chapters/intro.tex"},
        headers={"X-Dev-User-Id": you.id},
    )
    assert second_intro.json()["content"] == intro.decode()


async def test_a_non_member_gets_404_on_export(
    client: AsyncClient, db_session: AsyncSession, project: Project, you: User
):
    created = await _import(client, you, project.id, _zip({"main.tex": DOC}))
    stranger = (
        await db_session.execute(select(User).where(User.email == "marco@lab.io"))
    ).scalar_one()

    resp = await client.get(
        f"/v1/projects/{project.id}/latex/{created.json()['id']}/export",
        headers={"X-Dev-User-Id": stranger.id},
    )
    assert resp.status_code == 404


async def test_exporting_a_document_whose_name_contains_a_newline_still_returns_a_zip(
    client: AsyncClient, you: User, project: Project
):
    created = await _import(client, you, project.id, _zip({"main.tex": DOC}))
    doc_id = created.json()["id"]
    patched = await client.patch(
        f"/v1/projects/{project.id}/latex/{doc_id}",
        json={"name": "line1\nline2"},
        headers={"X-Dev-User-Id": you.id},
    )
    assert patched.status_code == 200

    resp = await client.get(
        f"/v1/projects/{project.id}/latex/{doc_id}/export",
        headers={"X-Dev-User-Id": you.id},
    )
    assert resp.status_code == 200
    disposition = resp.headers["content-disposition"]
    # The real bug this guards: a raw newline in a header value crashes
    # uvicorn's httptools AFTER the 200 status line is already sent (a reset
    # connection, not a clean error) -- invisible to this in-process ASGI
    # test client, which happily returns the header verbatim. Assert on the
    # header's own bytes, not just that the response completed.
    assert "\n" not in disposition
    assert "\r" not in disposition
    assert disposition == "attachment; filename=\"line1line2.zip\"; filename*=UTF-8''line1line2.zip"
    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
        assert sorted(z.namelist()) == [".researcherx.json", "main.tex"]


async def test_exporting_a_document_with_a_non_ascii_name_sets_an_rfc6266_filename(
    client: AsyncClient, you: User, project: Project
):
    created = await _import(client, you, project.id, _zip({"main.tex": DOC}))
    doc_id = created.json()["id"]
    patched = await client.patch(
        f"/v1/projects/{project.id}/latex/{doc_id}",
        json={"name": "café"},
        headers={"X-Dev-User-Id": you.id},
    )
    assert patched.status_code == 200

    resp = await client.get(
        f"/v1/projects/{project.id}/latex/{doc_id}/export",
        headers={"X-Dev-User-Id": you.id},
    )
    assert resp.status_code == 200
    disposition = resp.headers["content-disposition"]
    assert "filename*=UTF-8''caf%C3%A9.zip" in disposition
    ascii_part = disposition.split("filename=")[1].split(";")[0].strip()
    assert ascii_part == '"caf.zip"'
    assert "café" not in disposition


async def test_a_viewer_can_export(
    client: AsyncClient, db_session: AsyncSession, project: Project, you: User
):
    # Project membership is binary (owner/member) -- a project "member" who
    # did not import this document and holds no grant on it resolves to
    # document-level "viewer" (services/latex_access.py), and export is a
    # read.
    viewer = (
        await db_session.execute(select(User).where(User.email == "amelia@lab.io"))
    ).scalar_one()
    db_session.add(ProjectMember(project_id=project.id, user_id=viewer.id, role="member"))
    await db_session.commit()

    created = await _import(
        client, you, project.id, _zip({"main.tex": DOC, "chapters/intro.tex": b"\\section{I}"})
    )
    doc_id = created.json()["id"]

    resp = await client.get(
        f"/v1/projects/{project.id}/latex/{doc_id}/export",
        headers={"X-Dev-User-Id": viewer.id},
    )
    assert resp.status_code == 200
    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
        assert sorted(z.namelist()) == [".researcherx.json", "chapters/intro.tex", "main.tex"]


async def test_a_binary_tex_file_is_never_chosen_as_the_main_file(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    """A .tex file can decode cleanly as UTF-8 while still containing a NUL
    (classify_binary's tell). detect_main must never see it as a candidate --
    every other writer of main_path already blocks a binary target."""
    poisoned = b"\\documentclass{article}\x00\\begin{document}\\end{document}"
    resp = await _import(client, you, project.id, _zip({"main.tex": poisoned}))
    assert resp.status_code == 422
    assert (await db_session.execute(select(LatexDocument))).scalars().all() == []


async def test_an_ambiguous_main_document_round_trips_without_a_422(
    client: AsyncClient, you: User, project: Project
):
    blob = _zip({"a.tex": DOC, "b.tex": DOC})
    first = await _import(client, you, project.id, blob, main_path="b.tex")
    assert first.json()["main_path"] == "b.tex"

    exported = await client.get(
        f"/v1/projects/{project.id}/latex/{first.json()['id']}/export",
        headers={"X-Dev-User-Id": you.id},
    )
    second = await _import(client, you, project.id, exported.content)
    assert second.status_code == 201
    assert second.json()["main_path"] == "b.tex"


async def test_a_xelatex_document_round_trips_as_xelatex(
    client: AsyncClient, you: User, project: Project
):
    src = b"\\documentclass{article}\n\\usepackage{fontspec}\n\\begin{document}\\end{document}"
    first = await _import(client, you, project.id, _zip({"main.tex": src}))
    assert first.json()["engine"] == "xelatex"

    # PATCH to xelatex without a triggering package -- detection alone would
    # come back pdflatex on re-import; the manifest is what preserves it.
    plain = b"\\documentclass{article}\n\\begin{document}\\end{document}"
    plain_doc = await _import(client, you, project.id, _zip({"main.tex": plain}))
    patched = await client.patch(
        f"/v1/projects/{project.id}/latex/{plain_doc.json()['id']}",
        json={"engine": "xelatex"},
        headers={"X-Dev-User-Id": you.id},
    )
    assert patched.status_code == 200

    exported = await client.get(
        f"/v1/projects/{project.id}/latex/{plain_doc.json()['id']}/export",
        headers={"X-Dev-User-Id": you.id},
    )
    reimported = await _import(client, you, project.id, exported.content)
    assert reimported.status_code == 201
    assert reimported.json()["engine"] == "xelatex"


async def test_an_all_under_src_tree_round_trips_with_paths_unchanged(
    client: AsyncClient, you: User, project: Project
):
    """The tree must genuinely have `src/`-prefixed paths WITHOUT going
    through zip-wrapper stripping to get there -- importing a zip whose
    files are all under one top-level directory is *supposed* to strip that
    directory (`_common_prefix`), so a zip built that way is not a case of
    "all under src/", it is the wrapper-stripping test wearing a different
    directory name. Built here by renaming/writing into an already-imported
    document instead, the same way a user's editor session would arrive at
    an all-under-src/ tree."""
    first = await _import(client, you, project.id, _zip({"main.tex": DOC}))
    doc_id = first.json()["id"]
    renamed = await client.post(
        f"/v1/projects/{project.id}/latex/{doc_id}/file/rename",
        json={"from_path": "main.tex", "to_path": "src/main.tex"},
        headers={"X-Dev-User-Id": you.id},
    )
    assert renamed.status_code == 200
    written = await client.put(
        f"/v1/projects/{project.id}/latex/{doc_id}/file",
        params={"path": "src/chapters/intro.tex"},
        json={"content": "\\section{I}"},
        headers={"X-Dev-User-Id": you.id},
    )
    assert written.status_code == 200

    exported = await client.get(
        f"/v1/projects/{project.id}/latex/{doc_id}/export",
        headers={"X-Dev-User-Id": you.id},
    )
    with zipfile.ZipFile(io.BytesIO(exported.content)) as z:
        assert sorted(z.namelist()) == [
            ".researcherx.json",
            "src/chapters/intro.tex",
            "src/main.tex",
        ]

    second = await _import(client, you, project.id, exported.content)
    assert second.status_code == 201
    assert second.json()["main_path"] == "src/main.tex"

    tree = await client.get(
        f"/v1/projects/{project.id}/latex/{second.json()['id']}/files",
        headers={"X-Dev-User-Id": you.id},
    )
    assert sorted(f["path"] for f in tree.json()["files"]) == [
        "src/chapters/intro.tex",
        "src/main.tex",
    ]


async def test_a_manifest_naming_a_binary_main_path_falls_back_to_detection(
    client: AsyncClient, you: User, project: Project
):
    """A hand-crafted archive, not one of our own exports: the manifest is
    attacker-supplied like every other archive byte, and its main_path gets
    the same guard as the explicit query override."""
    manifest = json.dumps({"main_path": "f.png", "engine": "pdflatex"}).encode()
    blob = _zip({"main.tex": DOC, "f.png": b"\x89PNG\x00", MANIFEST_PATH: manifest})
    resp = await _import(client, you, project.id, blob)
    assert resp.status_code == 201
    assert resp.json()["main_path"] == "main.tex"


async def test_a_manifest_naming_a_non_tex_main_path_falls_back_to_detection(
    client: AsyncClient, you: User, project: Project
):
    manifest = json.dumps({"main_path": "notes.txt", "engine": "pdflatex"}).encode()
    blob = _zip({"main.tex": DOC, "notes.txt": b"hello", MANIFEST_PATH: manifest})
    resp = await _import(client, you, project.id, blob)
    assert resp.status_code == 201
    assert resp.json()["main_path"] == "main.tex"


async def test_a_malformed_manifest_is_ignored_not_fatal(
    client: AsyncClient, you: User, project: Project
):
    blob = _zip({"main.tex": DOC, MANIFEST_PATH: b"{not valid json"})
    resp = await _import(client, you, project.id, blob)
    assert resp.status_code == 201
    assert resp.json()["main_path"] == "main.tex"
    assert resp.json()["engine"] == "pdflatex"


async def test_a_manifest_with_an_invalid_engine_falls_back_to_detection(
    client: AsyncClient, you: User, project: Project
):
    manifest = json.dumps({"main_path": "main.tex", "engine": "not-a-real-engine"}).encode()
    blob = _zip({"main.tex": DOC, MANIFEST_PATH: manifest})
    resp = await _import(client, you, project.id, blob)
    assert resp.status_code == 201
    assert resp.json()["engine"] == "pdflatex"


async def test_the_manifest_never_appears_in_the_imported_tree_or_file_count(
    client: AsyncClient, you: User, project: Project
):
    manifest = json.dumps({"main_path": "main.tex", "engine": "pdflatex"}).encode()
    blob = _zip({"main.tex": DOC, MANIFEST_PATH: manifest})
    resp = await _import(client, you, project.id, blob)
    assert resp.status_code == 201
    assert resp.json()["file_count"] == 1

    tree = await client.get(
        f"/v1/projects/{project.id}/latex/{resp.json()['id']}/files",
        headers={"X-Dev-User-Id": you.id},
    )
    assert [f["path"] for f in tree.json()["files"]] == ["main.tex"]


async def test_an_explicit_main_path_query_param_overrides_the_manifest(
    client: AsyncClient, you: User, project: Project
):
    manifest = json.dumps({"main_path": "a.tex", "engine": "pdflatex"}).encode()
    blob = _zip({"a.tex": DOC, "b.tex": DOC, MANIFEST_PATH: manifest})
    resp = await _import(client, you, project.id, blob, main_path="b.tex")
    assert resp.status_code == 201
    assert resp.json()["main_path"] == "b.tex"


async def test_concurrent_imports_are_bounded_by_the_semaphore(
    client: AsyncClient, you: User, project: Project
):
    """A fresh Semaphore(2) patched over the module-level one, same pattern
    `test_latex_compiler_client.py` uses for `_compile_semaphore`: an
    asyncio.Semaphore binds to the event loop that first contends it, and
    pytest hands each test a fresh loop, so the production object cannot be
    contended directly here. Without the semaphore gating `read_archive` in
    the plan route -- the only route that parses an archive now -- all four
    requests below would enter it immediately and `max_entered` would reach
    4, not 2."""
    import asyncio
    import threading
    from unittest.mock import patch

    from app.api.v1 import latex_files as route_module
    from app.services.latex_archive import ArchiveEntry

    limit = asyncio.Semaphore(2)
    entered = 0
    max_entered = 0
    lock = threading.Lock()
    release = threading.Event()
    entered_two = threading.Event()

    def fake_read_archive(blob: bytes) -> list[ArchiveEntry]:
        nonlocal entered, max_entered
        with lock:
            entered += 1
            max_entered = max(max_entered, entered)
            if entered == 2:
                entered_two.set()
        release.wait(timeout=5)
        with lock:
            entered -= 1
        return [ArchiveEntry(path="main.tex", data=DOC, is_binary=False)]

    with (
        patch.object(route_module, "_import_semaphore", limit),
        patch.object(route_module, "read_archive", fake_read_archive),
    ):
        tasks = [
            asyncio.create_task(
                client.post(
                    f"/v1/projects/{project.id}/latex/import/plan?name=doc{i}",
                    content=b"irrelevant -- read_archive is faked",
                    headers=_h(you),
                )
            )
            for i in range(4)
        ]
        loop = asyncio.get_running_loop()
        got_two = await loop.run_in_executor(None, entered_two.wait, 5)
        assert got_two
        # A wrongly-admitted third task would enter `fake_read_archive`
        # immediately after `entered_two` fires; give it a chance to.
        await asyncio.sleep(0.05)
        assert entered == 2

        release.set()
        responses = await asyncio.gather(*tasks)

    assert max_entered == 2
    for r in responses:
        assert r.status_code == 200


# --- Corrective fix: regressions introduced by the manifest surface itself ---


async def test_a_manifest_engine_that_is_a_list_falls_back_to_detected_engine(
    client: AsyncClient, you: User, project: Project
):
    """`obj.get("engine")` can be ANY JSON type, not just a string -- a list
    is unhashable and must never reach the `in _VALID_ENGINES` frozenset
    check un-type-checked. Must degrade to detection, not 500."""
    manifest = json.dumps({"main_path": "main.tex", "engine": ["xelatex"]}).encode()
    blob = _zip({"main.tex": DOC, MANIFEST_PATH: manifest})
    resp = await _import(client, you, project.id, blob)
    assert resp.status_code == 201
    assert resp.json()["engine"] == "pdflatex"


async def test_a_manifest_engine_that_is_a_dict_falls_back_to_detected_engine(
    client: AsyncClient, you: User, project: Project
):
    manifest = json.dumps({"main_path": "main.tex", "engine": {}}).encode()
    blob = _zip({"main.tex": DOC, MANIFEST_PATH: manifest})
    resp = await _import(client, you, project.id, blob)
    assert resp.status_code == 201
    assert resp.json()["engine"] == "pdflatex"


async def test_a_deeply_nested_manifest_body_imports_rather_than_500ing(
    client: AsyncClient, you: User, project: Project
):
    """`json.loads` on attacker-controlled JSON can raise more than
    JSONDecodeError -- a `[` repeated deeply enough blows the C parser's
    recursion limit with a bare RecursionError."""
    manifest = b"[" * 200_000
    blob = _zip({"main.tex": DOC, MANIFEST_PATH: manifest})
    resp = await _import(client, you, project.id, blob)
    assert resp.status_code == 201
    assert resp.json()["main_path"] == "main.tex"


async def test_writing_a_file_at_the_manifest_path_is_a_422(
    client: AsyncClient, you: User, project: Project
):
    created = await _import(client, you, project.id, _zip({"main.tex": DOC}))
    doc_id = created.json()["id"]
    resp = await client.put(
        f"/v1/projects/{project.id}/latex/{doc_id}/file",
        params={"path": MANIFEST_PATH},
        json={"content": "not a manifest"},
        headers={"X-Dev-User-Id": you.id},
    )
    assert resp.status_code == 422


async def test_writing_a_binary_file_at_the_manifest_path_is_a_422(
    client: AsyncClient, you: User, project: Project
):
    created = await _import(client, you, project.id, _zip({"main.tex": DOC}))
    doc_id = created.json()["id"]
    resp = await client.post(
        f"/v1/projects/{project.id}/latex/{doc_id}/file/binary",
        params={"path": MANIFEST_PATH},
        content=b"\x89PNG\x00",
        headers={"X-Dev-User-Id": you.id},
    )
    assert resp.status_code == 422


async def test_renaming_a_file_onto_the_manifest_path_is_a_422(
    client: AsyncClient, you: User, project: Project
):
    created = await _import(client, you, project.id, _zip({"main.tex": DOC, "notes.txt": b"hi"}))
    doc_id = created.json()["id"]
    resp = await client.post(
        f"/v1/projects/{project.id}/latex/{doc_id}/file/rename",
        json={"from_path": "notes.txt", "to_path": MANIFEST_PATH},
        headers={"X-Dev-User-Id": you.id},
    )
    assert resp.status_code == 422


async def test_a_reserved_path_document_can_no_longer_be_built_so_export_never_duplicates(
    client: AsyncClient, you: User, project: Project
):
    """The regression this guards: before the write-path guard, a user file
    at MANIFEST_PATH plus the unconditional manifest write on export produced
    a zip with a DUPLICATE member, and re-importing it 422'd on a collision
    with itself. Since a real file can no longer land at that path, this now
    just proves the tree stays at one member of that name -- the manifest --
    and the round trip stays clean."""
    created = await _import(client, you, project.id, _zip({"main.tex": DOC}))
    doc_id = created.json()["id"]
    blocked = await client.put(
        f"/v1/projects/{project.id}/latex/{doc_id}/file",
        params={"path": MANIFEST_PATH},
        json={"content": "not a manifest"},
        headers={"X-Dev-User-Id": you.id},
    )
    assert blocked.status_code == 422

    exported = await client.get(
        f"/v1/projects/{project.id}/latex/{doc_id}/export",
        headers={"X-Dev-User-Id": you.id},
    )
    with zipfile.ZipFile(io.BytesIO(exported.content)) as z:
        assert z.namelist().count(MANIFEST_PATH) == 1

    reimported = await _import(client, you, project.id, exported.content)
    assert reimported.status_code == 201


async def test_a_user_authored_manifest_named_file_that_is_not_valid_json_is_dropped_not_imported(
    client: AsyncClient, you: User, project: Project
):
    """An archive built before this rule existed, or crafted by hand, can
    still contain a real file at the reserved path. Import must not break --
    and must not let that entry land in the tree, valid manifest or not."""
    blob = _zip({"main.tex": DOC, MANIFEST_PATH: b"this is not JSON at all"})
    resp = await _import(client, you, project.id, blob)
    assert resp.status_code == 201
    assert resp.json()["file_count"] == 1

    tree = await client.get(
        f"/v1/projects/{project.id}/latex/{resp.json()['id']}/files",
        headers={"X-Dev-User-Id": you.id},
    )
    assert [f["path"] for f in tree.json()["files"]] == ["main.tex"]


# --- The two-step flow itself: plan, then commit against a staging token ---


async def test_plan_reports_no_collisions_for_a_fresh_document(
    client: AsyncClient, you: User, project: Project
):
    resp = await client.post(
        f"/v1/projects/{project.id}/latex/import/plan?name=Paper",
        content=_zip({"main.tex": DOC}),
        headers=_h(you),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "create"
    assert body["collisions"] == []
    assert body["file_count"] == 1
    assert body["staging_id"]


async def test_planning_creates_nothing(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    """The whole point of a plan step: the user is told what will happen
    BEFORE anything happens."""
    resp = await client.post(
        f"/v1/projects/{project.id}/latex/import/plan?name=Paper",
        content=_zip({"main.tex": DOC}),
        headers=_h(you),
    )
    assert resp.status_code == 200
    assert (await db_session.execute(select(LatexDocument))).scalars().all() == []


async def test_plan_reports_collisions_for_a_merge(
    client: AsyncClient, you: User, project: Project
):
    created = await _import(client, you, project.id, _zip({"main.tex": DOC}))
    doc_id = created.json()["id"]

    resp = await client.post(
        f"/v1/projects/{project.id}/latex/import/plan?document_id={doc_id}",
        content=_zip({"main.tex": b"incoming", "extra.tex": b"new"}),
        headers=_h(you),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "merge"
    assert body["collisions"] == [
        {"path": "main.tex", "existing": "main.tex", "suggestion": "main (1).tex"}
    ]


async def test_commit_applies_the_decisions_and_leaves_the_original(
    client: AsyncClient, you: User, project: Project
):
    created = await _import(client, you, project.id, _zip({"main.tex": DOC}))
    doc_id = created.json()["id"]
    plan = await client.post(
        f"/v1/projects/{project.id}/latex/import/plan?document_id={doc_id}",
        content=_zip({"main.tex": b"incoming"}),
        headers=_h(you),
    )

    resp = await client.post(
        f"/v1/projects/{project.id}/latex/import/commit",
        json={
            "staging_id": plan.json()["staging_id"],
            "document_id": doc_id,
            "decisions": [{"path": "main.tex", "new_path": "main (1).tex"}],
        },
        headers={"X-Dev-User-Id": you.id},
    )

    assert resp.status_code == 201
    tree = await client.get(
        f"/v1/projects/{project.id}/latex/{doc_id}/files",
        headers={"X-Dev-User-Id": you.id},
    )
    assert sorted(f["path"] for f in tree.json()["files"]) == ["main (1).tex", "main.tex"]
    kept = await client.get(
        f"/v1/projects/{project.id}/latex/{doc_id}/file",
        params={"path": "main.tex"},
        headers={"X-Dev-User-Id": you.id},
    )
    assert kept.json()["content"] == DOC.decode()


async def test_a_merge_leaves_the_documents_own_main_path_alone(
    client: AsyncClient, you: User, project: Project
):
    """A merge adds files. The archive's idea of which file is main has no
    authority over a document that already compiles."""
    created = await _import(client, you, project.id, _zip({"main.tex": DOC}))
    doc_id = created.json()["id"]
    plan = await client.post(
        f"/v1/projects/{project.id}/latex/import/plan?document_id={doc_id}",
        content=_zip({"other.tex": DOC}),
        headers=_h(you),
    )
    resp = await client.post(
        f"/v1/projects/{project.id}/latex/import/commit",
        json={"staging_id": plan.json()["staging_id"], "document_id": doc_id},
        headers={"X-Dev-User-Id": you.id},
    )
    assert resp.status_code == 201
    assert resp.json()["main_path"] == "main.tex"


async def test_committing_a_merge_whose_collision_was_not_resolved_is_a_409(
    client: AsyncClient, you: User, project: Project
):
    """The plan said what collides; a commit that ignores it must not
    overwrite. The 409 carries the same structured suggestion every other
    collision does."""
    created = await _import(client, you, project.id, _zip({"main.tex": DOC}))
    doc_id = created.json()["id"]
    plan = await client.post(
        f"/v1/projects/{project.id}/latex/import/plan?document_id={doc_id}",
        content=_zip({"main.tex": b"incoming"}),
        headers=_h(you),
    )

    resp = await client.post(
        f"/v1/projects/{project.id}/latex/import/commit",
        json={"staging_id": plan.json()["staging_id"], "document_id": doc_id},
        headers={"X-Dev-User-Id": you.id},
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "path_collision"


async def test_a_staging_token_is_single_use(client: AsyncClient, you: User, project: Project):
    created = await _import(client, you, project.id, _zip({"main.tex": DOC}))
    doc_id = created.json()["id"]
    plan = await client.post(
        f"/v1/projects/{project.id}/latex/import/plan?document_id={doc_id}",
        content=_zip({"a.tex": b"a"}),
        headers=_h(you),
    )
    body = {"staging_id": plan.json()["staging_id"], "document_id": doc_id}
    first = await client.post(
        f"/v1/projects/{project.id}/latex/import/commit",
        json=body,
        headers={"X-Dev-User-Id": you.id},
    )
    assert first.status_code == 201

    resp = await client.post(
        f"/v1/projects/{project.id}/latex/import/commit",
        json=body,
        headers={"X-Dev-User-Id": you.id},
    )

    assert resp.status_code == 404


async def test_an_expired_staging_token_is_a_410_not_a_404(
    client: AsyncClient, you: User, project: Project, monkeypatch
):
    """Distinguishable on purpose: an expired upload needs a different
    sentence from an unknown one -- choose the .zip again, rather than
    something went wrong."""
    plan = await client.post(
        f"/v1/projects/{project.id}/latex/import/plan?name=Paper",
        content=_zip({"main.tex": DOC}),
        headers=_h(you),
    )
    # The TTL is read once, at construction, so the live singleton is what
    # has to be aged. Time itself is a parameter of `take`, so nothing here
    # sleeps.
    monkeypatch.setattr(staging, "_ttl_s", -1)

    resp = await client.post(
        f"/v1/projects/{project.id}/latex/import/commit",
        json={"staging_id": plan.json()["staging_id"]},
        headers={"X-Dev-User-Id": you.id},
    )

    assert resp.status_code == 410


async def test_another_user_cannot_redeem_a_staging_token(
    client: AsyncClient, db_session: AsyncSession, you: User, project: Project
):
    member = (
        await db_session.execute(select(User).where(User.email == "amelia@lab.io"))
    ).scalar_one()
    db_session.add(ProjectMember(project_id=project.id, user_id=member.id, role="member"))
    await db_session.commit()
    plan = await client.post(
        f"/v1/projects/{project.id}/latex/import/plan?name=Paper",
        content=_zip({"main.tex": DOC}),
        headers=_h(you),
    )

    resp = await client.post(
        f"/v1/projects/{project.id}/latex/import/commit",
        json={"staging_id": plan.json()["staging_id"]},
        headers={"X-Dev-User-Id": member.id},
    )

    assert resp.status_code == 404


async def test_a_decision_naming_a_path_not_in_the_archive_is_refused(
    client: AsyncClient, you: User, project: Project
):
    plan = await client.post(
        f"/v1/projects/{project.id}/latex/import/plan?name=Paper",
        content=_zip({"main.tex": DOC}),
        headers=_h(you),
    )

    resp = await client.post(
        f"/v1/projects/{project.id}/latex/import/commit",
        json={
            "staging_id": plan.json()["staging_id"],
            "decisions": [{"path": "../escape.tex", "new_path": "x.tex"}],
        },
        headers={"X-Dev-User-Id": you.id},
    )

    assert resp.status_code == 422


async def test_a_decision_new_path_goes_through_the_traversal_guard(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    # A decision is user input from the same untrusted surface as any other
    # write. `normalize_path` is the one guard and it applies here too.
    plan = await client.post(
        f"/v1/projects/{project.id}/latex/import/plan?name=Paper",
        content=_zip({"main.tex": DOC}),
        headers=_h(you),
    )

    resp = await client.post(
        f"/v1/projects/{project.id}/latex/import/commit",
        json={
            "staging_id": plan.json()["staging_id"],
            "decisions": [{"path": "main.tex", "new_path": "../../etc/passwd"}],
        },
        headers={"X-Dev-User-Id": you.id},
    )

    assert resp.status_code == 422
    assert (await db_session.execute(select(LatexDocument))).scalars().all() == []


async def test_a_decision_naming_the_reserved_manifest_path_is_a_422(
    client: AsyncClient, you: User, project: Project
):
    plan = await client.post(
        f"/v1/projects/{project.id}/latex/import/plan?name=Paper",
        content=_zip({"main.tex": DOC}),
        headers=_h(you),
    )

    resp = await client.post(
        f"/v1/projects/{project.id}/latex/import/commit",
        json={
            "staging_id": plan.json()["staging_id"],
            "decisions": [{"path": "main.tex", "new_path": MANIFEST_PATH}],
        },
        headers={"X-Dev-User-Id": you.id},
    )

    assert resp.status_code == 422


async def test_a_create_decision_carries_the_main_file_with_it(
    client: AsyncClient, you: User, project: Project
):
    """A renamed main file must still be the document's main file -- the
    alternative is a document whose main_path names nothing."""
    plan = await client.post(
        f"/v1/projects/{project.id}/latex/import/plan?name=Paper",
        content=_zip({"main.tex": DOC}),
        headers=_h(you),
    )

    resp = await client.post(
        f"/v1/projects/{project.id}/latex/import/commit",
        json={
            "staging_id": plan.json()["staging_id"],
            "name": "Paper",
            "decisions": [{"path": "main.tex", "new_path": "main (1).tex"}],
        },
        headers={"X-Dev-User-Id": you.id},
    )

    assert resp.status_code == 201
    assert resp.json()["main_path"] == "main (1).tex"


async def test_plan_reports_a_duplicate_document_name(
    client: AsyncClient, you: User, project: Project
):
    await _import(client, you, project.id, _zip({"main.tex": DOC}), name="paper")

    resp = await client.post(
        f"/v1/projects/{project.id}/latex/import/plan?name=paper",
        content=_zip({"main.tex": DOC}),
        headers=_h(you),
    )

    assert resp.json()["name_collision"] == {"name": "paper", "suggestion": "paper (1)"}


async def test_a_merge_is_not_asked_about_a_document_name(
    client: AsyncClient, you: User, project: Project
):
    """A merge writes into a document that already has a name; there is
    nothing to name and nothing to ask."""
    created = await _import(client, you, project.id, _zip({"main.tex": DOC}), name="paper")

    resp = await client.post(
        f"/v1/projects/{project.id}/latex/import/plan"
        f"?document_id={created.json()['id']}&name=paper",
        content=_zip({"a.tex": b"a"}),
        headers=_h(you),
    )

    assert resp.json()["name_collision"] is None


async def test_plan_reports_ambiguous_main_instead_of_a_422(
    client: AsyncClient, you: User, project: Project
):
    # The plan step is where the client learns everything it must ask the
    # user about; asking twice in two different shapes is worse.
    resp = await client.post(
        f"/v1/projects/{project.id}/latex/import/plan?name=Paper",
        content=_zip({"a.tex": DOC, "b.tex": DOC}),
        headers=_h(you),
    )

    assert resp.status_code == 200
    assert sorted(resp.json()["ambiguous_main"]) == ["a.tex", "b.tex"]


async def test_an_ambiguity_answered_on_commit_needs_no_second_upload(
    client: AsyncClient, you: User, project: Project
):
    plan = await client.post(
        f"/v1/projects/{project.id}/latex/import/plan?name=Paper",
        content=_zip({"a.tex": DOC, "b.tex": DOC}),
        headers=_h(you),
    )

    resp = await client.post(
        f"/v1/projects/{project.id}/latex/import/commit",
        json={
            "staging_id": plan.json()["staging_id"],
            "name": "Paper",
            "main_path": "b.tex",
        },
        headers={"X-Dev-User-Id": you.id},
    )

    assert resp.status_code == 201
    assert resp.json()["main_path"] == "b.tex"


async def test_a_non_member_gets_404_on_plan(
    client: AsyncClient, db_session: AsyncSession, project: Project
):
    stranger = (
        await db_session.execute(select(User).where(User.email == "marco@lab.io"))
    ).scalar_one()
    resp = await client.post(
        f"/v1/projects/{project.id}/latex/import/plan?name=Paper",
        content=_zip({"main.tex": DOC}),
        headers=_h(stranger),
    )
    assert resp.status_code == 404


async def test_planning_a_merge_into_a_document_in_another_project_is_a_404(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    other = Project(owner_id=you.id, title="Other", topic_keywords=[])
    db_session.add(other)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=other.id, user_id=you.id, role="owner"))
    await db_session.commit()
    created = await _import(client, you, other.id, _zip({"main.tex": DOC}))

    resp = await client.post(
        f"/v1/projects/{project.id}/latex/import/plan?document_id={created.json()['id']}",
        content=_zip({"a.tex": b"a"}),
        headers=_h(you),
    )

    assert resp.status_code == 404
