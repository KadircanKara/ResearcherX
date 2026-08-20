"""LaTeX document routes. Membership is enforced on every one of them."""

from unittest.mock import AsyncMock, patch

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LatexDocument, Project, ProjectMember, User
from app.db.seed import seed_users
from app.services.latex_cache import CachedBuild, LatexCache, source_hash
from app.services.latex_compiler import CompileResult, PdfPosition, SourcePoint
from app.services.latex_files_service import tree_hash


@pytest_asyncio.fixture
async def seeded(db_session: AsyncSession):
    await seed_users(db_session)
    await db_session.commit()


@pytest_asyncio.fixture
async def you(db_session: AsyncSession, seeded):
    return (
        await db_session.execute(select(User).where(User.email == "you@researcherx.dev"))
    ).scalar_one()


@pytest_asyncio.fixture
async def project(db_session: AsyncSession, you: User) -> Project:
    p = Project(owner_id=you.id, title="LaTeX API Test", topic_keywords=[])
    db_session.add(p)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=p.id, user_id=you.id, role="owner"))
    await db_session.commit()
    await db_session.refresh(p)
    return p


async def test_create_and_list_documents(client: AsyncClient, you: User, project: Project):
    created = await client.post(
        f"/v1/projects/{project.id}/latex",
        json={"name": "main.tex", "source": "\\documentclass{article}"},
        headers={"X-Dev-User-Id": you.id},
    )
    assert created.status_code == 201

    listed = await client.get(f"/v1/projects/{project.id}/latex", headers={"X-Dev-User-Id": you.id})
    assert [d["name"] for d in listed.json()] == ["main.tex"]


async def test_patch_saves_the_source(client: AsyncClient, you: User, project: Project):
    created = await client.post(
        f"/v1/projects/{project.id}/latex",
        json={"name": "main.tex", "source": "old"},
        headers={"X-Dev-User-Id": you.id},
    )
    doc_id = created.json()["id"]

    resp = await client.patch(
        f"/v1/projects/{project.id}/latex/{doc_id}",
        json={"source": "new"},
        headers={"X-Dev-User-Id": you.id},
    )

    assert resp.status_code == 200
    assert resp.json()["source"] == "new"


async def test_a_document_from_another_project_404s(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    """Resolved the same way conversations already are: a foreign id is
    indistinguishable from a missing one."""
    other = Project(owner_id=you.id, title="Other", topic_keywords=[])
    db_session.add(other)
    await db_session.flush()
    foreign = LatexDocument(project_id=other.id, name="theirs.tex")
    db_session.add(foreign)
    await db_session.commit()
    await db_session.refresh(foreign)

    resp = await client.get(
        f"/v1/projects/{project.id}/latex/{foreign.id}", headers={"X-Dev-User-Id": you.id}
    )

    assert resp.status_code == 404


async def test_a_non_member_cannot_read_documents(
    client: AsyncClient, project: Project, db_session: AsyncSession, seeded
):
    stranger = (
        (await db_session.execute(select(User).where(User.email != "you@researcherx.dev")))
        .scalars()
        .first()
    )

    resp = await client.get(
        f"/v1/projects/{project.id}/latex", headers={"X-Dev-User-Id": stranger.id}
    )

    assert resp.status_code in (403, 404)


async def test_delete_removes_the_document(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    doc = LatexDocument(project_id=project.id, name="gone.tex")
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)

    resp = await client.delete(
        f"/v1/projects/{project.id}/latex/{doc.id}", headers={"X-Dev-User-Id": you.id}
    )

    assert resp.status_code == 204
    remaining = (
        await db_session.execute(select(LatexDocument).where(LatexDocument.id == doc.id))
    ).scalar_one_or_none()
    assert remaining is None


async def test_compile_stores_the_pdf_and_returns_its_hash(
    client: AsyncClient, you: User, project: Project
):
    created = await client.post(
        f"/v1/projects/{project.id}/latex",
        json={"name": "main.tex", "source": "\\documentclass{article}"},
        headers={"X-Dev-User-Id": you.id},
    )
    doc_id = created.json()["id"]

    result = CompileResult(ok=True, log="", pdf=b"%PDF-good", synctex_gz=b"gz", root="/tmp/rx-1")
    with (
        patch("app.api.v1.latex.compile_tree", AsyncMock(return_value=result)),
        patch("app.api.v1.latex.cache", LatexCache(max_entries=4, max_bytes=10_000)) as cache,
    ):
        resp = await client.post(
            f"/v1/projects/{project.id}/latex/{doc_id}/compile",
            headers={"X-Dev-User-Id": you.id},
        )
        body = resp.json()
        pdf = await client.get(
            f"/v1/projects/{project.id}/latex/{doc_id}/pdf?hash={body['pdf_hash']}",
            headers={"X-Dev-User-Id": you.id},
        )

    assert body["ok"] is True
    assert body["pdf_hash"] == tree_hash(
        [("main.tex", b"\\documentclass{article}")], "pdflatex", "main.tex"
    )
    assert pdf.content == b"%PDF-good"
    assert cache.get(body["pdf_hash"]) is not None


async def test_a_failed_compile_returns_the_log_and_no_hash(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    """No hash means the client keeps showing the PDF it already has -- the
    last good PDF survives a broken edit."""
    doc = LatexDocument(project_id=project.id, name="main.tex")
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)

    result = CompileResult(ok=False, log="! Undefined control sequence.", pdf=None, synctex_gz=None)
    with patch("app.api.v1.latex.compile_tree", AsyncMock(return_value=result)):
        resp = await client.post(
            f"/v1/projects/{project.id}/latex/{doc.id}/compile",
            headers={"X-Dev-User-Id": you.id},
        )

    body = resp.json()
    assert body["ok"] is False
    assert body["pdf_hash"] is None
    assert "Undefined control sequence" in body["log"]


async def test_fetching_a_pdf_hash_that_is_not_cached_404s(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    doc = LatexDocument(project_id=project.id, name="main.tex")
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)

    resp = await client.get(
        f"/v1/projects/{project.id}/latex/{doc.id}/pdf?hash=deadbeef",
        headers={"X-Dev-User-Id": you.id},
    )

    assert resp.status_code == 404


async def test_forward_sync_maps_a_line_to_a_page_position(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    doc = LatexDocument(project_id=project.id, name="main.tex")
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)

    prepared = LatexCache(max_entries=4, max_bytes=10_000)
    prepared.put(
        source_hash("src", "pdflatex"),
        CachedBuild(
            pdf=b"%PDF", synctex_gz=b"gz", log="", root="/tmp/rx-latex-abc", main_path="main.tex"
        ),
        document_id=doc.id,
    )
    position = PdfPosition(page=1, x=36.0, y=122.0, width=100.0, height=12.0)

    with (
        patch("app.api.v1.latex.cache", prepared),
        patch("app.api.v1.latex.synctex_forward", AsyncMock(return_value=position)) as forward,
    ):
        resp = await client.post(
            f"/v1/projects/{project.id}/latex/{doc.id}/synctex/forward",
            json={"line": 161},
            headers={"X-Dev-User-Id": you.id},
        )

    # No `file` in the request body -> the route defaults it to the LAST
    # COMPILED build's main_path (not the document's current one), which is
    # also what keeps the pre-existing single-file frontend (which sends no
    # `file` at all) working unchanged. This replaces the old TRANSITIONAL
    # pin of root=None/main_path=""/file="" from before the route threaded
    # the tree through -- the real values now flow all the way to the
    # compiler client call.
    forward.assert_called_once_with(
        b"%PDF", b"gz", root="/tmp/rx-latex-abc", main_path="main.tex", file="main.tex", line=161
    )
    assert resp.json() == {
        "found": True,
        "page": 1,
        "x": 36.0,
        "y": 122.0,
        "width": 100.0,
        "height": 12.0,
    }


async def test_forward_sync_with_an_explicit_file_passes_it_through(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    """A chapter's own double-click sends its own path -- the route must not
    override an explicit `file` with the build's main_path."""
    doc = LatexDocument(project_id=project.id, name="main.tex")
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)

    prepared = LatexCache(max_entries=4, max_bytes=10_000)
    prepared.put(
        source_hash("src", "pdflatex"),
        CachedBuild(
            pdf=b"%PDF", synctex_gz=b"gz", log="", root="/tmp/rx-latex-abc", main_path="main.tex"
        ),
        document_id=doc.id,
    )
    position = PdfPosition(page=1, x=36.0, y=122.0, width=100.0, height=12.0)

    with (
        patch("app.api.v1.latex.cache", prepared),
        patch("app.api.v1.latex.synctex_forward", AsyncMock(return_value=position)) as forward,
    ):
        resp = await client.post(
            f"/v1/projects/{project.id}/latex/{doc.id}/synctex/forward",
            json={"line": 2, "file": "chapters/intro.tex"},
            headers={"X-Dev-User-Id": you.id},
        )

    forward.assert_called_once_with(
        b"%PDF",
        b"gz",
        root="/tmp/rx-latex-abc",
        main_path="main.tex",
        file="chapters/intro.tex",
        line=2,
    )
    assert resp.json()["found"] is True


async def test_sync_before_any_compile_reports_not_found_rather_than_erroring(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    """Navigation is an enhancement: with no build cached there is nothing to
    map, and the editor must keep working."""
    doc = LatexDocument(project_id=project.id, name="main.tex")
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)

    with patch("app.api.v1.latex.cache", LatexCache(max_entries=4, max_bytes=10_000)):
        resp = await client.post(
            f"/v1/projects/{project.id}/latex/{doc.id}/synctex/reverse",
            json={"page": 1, "x": 36.0, "y": 122.0},
            headers={"X-Dev-User-Id": you.id},
        )

    assert resp.status_code == 200
    assert resp.json() == {"found": False, "line": None, "file": None}


async def test_reverse_sync_maps_a_point_to_a_line(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    # Created through the API, not a raw ORM insert, so `main.tex` actually
    # exists in the tree -- the route now filters a reverse hit against the
    # tree, and a document with no files at all would answer `found: false`
    # for every point regardless of what synctex_reverse returns.
    created = await client.post(
        f"/v1/projects/{project.id}/latex",
        json={"name": "main.tex", "source": "src"},
        headers={"X-Dev-User-Id": you.id},
    )
    doc_id = created.json()["id"]

    prepared = LatexCache(max_entries=4, max_bytes=10_000)
    prepared.put(
        source_hash("src", "pdflatex"),
        CachedBuild(
            pdf=b"%PDF", synctex_gz=b"gz", log="", root="/tmp/rx-latex-abc", main_path="main.tex"
        ),
        document_id=doc_id,
    )

    with (
        patch("app.api.v1.latex.cache", prepared),
        patch(
            "app.api.v1.latex.synctex_reverse",
            AsyncMock(return_value=SourcePoint(file="main.tex", line=161)),
        ) as reverse,
    ):
        resp = await client.post(
            f"/v1/projects/{project.id}/latex/{doc_id}/synctex/reverse",
            json={"page": 1, "x": 36.0, "y": 122.0},
            headers={"X-Dev-User-Id": you.id},
        )

    # Replaces the old TRANSITIONAL pin of root=None/main_path="" from before
    # the route threaded the tree through -- the real cached build's root and
    # main_path now flow straight to the compiler client call.
    reverse.assert_called_once_with(
        b"%PDF", b"gz", root="/tmp/rx-latex-abc", main_path="main.tex", page=1, x=36.0, y=122.0
    )
    assert resp.json() == {"found": True, "line": 161, "file": "main.tex"}


async def test_a_reverse_result_naming_a_file_no_longer_in_the_tree_returns_cleanly(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    """The compiler cannot know the current tree -- it answers from the PDF
    and the map alone -- so it can hand back a build artifact (main.toc,
    main.aux) or a since-deleted source file as if it were still openable.
    A file the editor cannot open is worse than no answer: the route must
    filter this to `found: false` rather than pass it straight through."""
    doc = LatexDocument(project_id=project.id, name="main.tex")
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)

    prepared = LatexCache(max_entries=4, max_bytes=10_000)
    prepared.put(
        source_hash("src", "pdflatex"),
        CachedBuild(
            pdf=b"%PDF", synctex_gz=b"gz", log="", root="/tmp/rx-latex-abc", main_path="main.tex"
        ),
        document_id=doc.id,
    )

    with (
        patch("app.api.v1.latex.cache", prepared),
        patch(
            "app.api.v1.latex.synctex_reverse",
            # main.toc is never a real file in latex_files -- it is a build
            # artifact the compile produced beside the PDF.
            AsyncMock(return_value=SourcePoint(file="main.toc", line=1)),
        ),
    ):
        resp = await client.post(
            f"/v1/projects/{project.id}/latex/{doc.id}/synctex/reverse",
            json={"page": 1, "x": 36.0, "y": 122.0},
            headers={"X-Dev-User-Id": you.id},
        )

    assert resp.status_code == 200
    assert resp.json() == {"found": False, "line": None, "file": None}


async def test_a_cache_hit_lets_a_second_identical_document_sync(
    client: AsyncClient, you: User, project: Project
):
    """Two documents created from the same template compile to the same
    cache key. `_latest` is written only by `LatexCache.put`, and the
    cache-hit branch used to return before ever calling it -- so the second
    document's build was never recorded and its SyncTeX queries answered
    `found: False` forever, even though a correct PDF was on screen."""
    payload = {"name": "a.tex", "source": "\\documentclass{article}"}
    doc1_resp = await client.post(
        f"/v1/projects/{project.id}/latex", json=payload, headers={"X-Dev-User-Id": you.id}
    )
    doc2_resp = await client.post(
        f"/v1/projects/{project.id}/latex",
        json={**payload, "name": "b.tex"},
        headers={"X-Dev-User-Id": you.id},
    )
    doc1_id = doc1_resp.json()["id"]
    doc2_id = doc2_resp.json()["id"]

    result = CompileResult(ok=True, log="", pdf=b"%PDF-good", synctex_gz=b"gz", root="/tmp/rx-2")
    position = PdfPosition(page=1, x=1.0, y=2.0, width=3.0, height=4.0)
    with (
        patch("app.api.v1.latex.compile_tree", AsyncMock(return_value=result)),
        patch("app.api.v1.latex.cache", LatexCache(max_entries=4, max_bytes=10_000)),
        patch("app.api.v1.latex.synctex_forward", AsyncMock(return_value=position)),
    ):
        first = await client.post(
            f"/v1/projects/{project.id}/latex/{doc1_id}/compile",
            headers={"X-Dev-User-Id": you.id},
        )
        second = await client.post(
            f"/v1/projects/{project.id}/latex/{doc2_id}/compile",
            headers={"X-Dev-User-Id": you.id},
        )
        # Both documents' trees hold the identical, non-empty source above,
        # so they hash the same -- doc2's compile is a cache HIT, not a
        # second miss.
        assert second.json()["pdf_hash"] == first.json()["pdf_hash"]

        sync_doc2 = await client.post(
            f"/v1/projects/{project.id}/latex/{doc2_id}/synctex/forward",
            json={"line": 1},
            headers={"X-Dev-User-Id": you.id},
        )

    assert sync_doc2.json()["found"] is True


async def test_a_cache_hit_after_an_edit_and_undo_syncs_the_reverted_build_not_the_edit(
    client: AsyncClient, you: User, project: Project
):
    """Edit S1 -> S2 -> undo back to S1. The third compile is a cache hit
    for S1's key, but before the fix `_latest` still pointed at S2 (the
    last `put`) -- so a sync query answered from the WRONG build. That is
    a confidently wrong line, worse than admitting staleness."""
    created = await client.post(
        f"/v1/projects/{project.id}/latex",
        json={"name": "main.tex", "source": "S1"},
        headers={"X-Dev-User-Id": you.id},
    )
    doc_id = created.json()["id"]

    s1 = CompileResult(ok=True, log="", pdf=b"%PDF-S1", synctex_gz=b"gz-s1")
    s2 = CompileResult(ok=True, log="", pdf=b"%PDF-S2", synctex_gz=b"gz-s2")
    position = PdfPosition(page=1, x=0.0, y=0.0, width=0.0, height=0.0)

    with patch("app.api.v1.latex.cache", LatexCache(max_entries=4, max_bytes=10_000)):
        with patch("app.api.v1.latex.compile_tree", AsyncMock(return_value=s1)):
            compiled_s1 = await client.post(
                f"/v1/projects/{project.id}/latex/{doc_id}/compile",
                headers={"X-Dev-User-Id": you.id},
            )

        await client.patch(
            f"/v1/projects/{project.id}/latex/{doc_id}",
            json={"source": "S2"},
            headers={"X-Dev-User-Id": you.id},
        )
        with patch("app.api.v1.latex.compile_tree", AsyncMock(return_value=s2)):
            await client.post(
                f"/v1/projects/{project.id}/latex/{doc_id}/compile",
                headers={"X-Dev-User-Id": you.id},
            )

        await client.patch(
            f"/v1/projects/{project.id}/latex/{doc_id}",
            json={"source": "S1"},
            headers={"X-Dev-User-Id": you.id},
        )
        with (
            patch("app.api.v1.latex.compile_tree", AsyncMock(return_value=s1)),
            patch("app.api.v1.latex.synctex_forward", AsyncMock(return_value=position)) as forward,
        ):
            compiled_undo = await client.post(
                f"/v1/projects/{project.id}/latex/{doc_id}/compile",
                headers={"X-Dev-User-Id": you.id},
            )
            await client.post(
                f"/v1/projects/{project.id}/latex/{doc_id}/synctex/forward",
                json={"line": 1},
                headers={"X-Dev-User-Id": you.id},
            )

    # The undo recompile is a cache hit against S1's own earlier build.
    assert compiled_undo.json()["pdf_hash"] == compiled_s1.json()["pdf_hash"]
    # The sync call must be handed S1's PDF/SyncTeX bytes, not S2's -- this
    # is the "confidently wrong" failure mode, not merely "not found".
    forward.assert_called_once()
    # synctex_forward's signature moved to (pdf, synctex_gz, *, root, main_path,
    # file, line) -- pdf/synctex_gz are still positional, the rest are kwargs.
    called_pdf, called_synctex = forward.call_args.args
    assert called_pdf == b"%PDF-S1"
    assert called_synctex == b"gz-s1"


async def test_an_oversized_source_is_rejected_with_a_422_not_silently_stored(
    client: AsyncClient, you: User, project: Project
):
    """Unbounded, this reaches the compile service and fails there with the
    generic 'unavailable' message -- measured live against a real 6MB
    source. A 422 at the edge names the actual problem (too large) instead
    of reading as an infra outage the user retries forever."""
    resp = await client.post(
        f"/v1/projects/{project.id}/latex",
        json={"name": "huge.tex", "source": "x" * 2_000_001},
        headers={"X-Dev-User-Id": you.id},
    )

    assert resp.status_code == 422
    assert any(err["type"] == "string_too_long" for err in resp.json()["detail"])


async def test_an_oversized_source_is_also_rejected_on_update(
    client: AsyncClient, you: User, project: Project, db_session: AsyncSession
):
    """The same bound applies to an edit growing a document past the limit,
    not just to creation -- an editor pasting a huge block must get the same
    real-cause 422, not a generic compiler-unavailable message five steps
    later."""
    doc = LatexDocument(project_id=project.id, name="main.tex")
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)

    resp = await client.patch(
        f"/v1/projects/{project.id}/latex/{doc.id}",
        json={"source": "x" * 2_000_001},
        headers={"X-Dev-User-Id": you.id},
    )

    assert resp.status_code == 422
    assert any(err["type"] == "string_too_long" for err in resp.json()["detail"])


async def test_creating_a_document_puts_its_source_in_the_tree_as_main_tex(
    client: AsyncClient, you: User, project: Project
):
    """The `source` field is a compatibility shim over the tree. Creating with
    it must produce a real file, or the editor and the compiler disagree."""
    created = await client.post(
        f"/v1/projects/{project.id}/latex",
        json={"name": "paper", "source": "\\documentclass{article}"},
        headers={"X-Dev-User-Id": you.id},
    )
    doc_id = created.json()["id"]
    assert created.json()["main_path"] == "main.tex"

    tree = await client.get(
        f"/v1/projects/{project.id}/latex/{doc_id}/files",
        headers={"X-Dev-User-Id": you.id},
    )
    assert [f["path"] for f in tree.json()["files"]] == ["main.tex"]


async def test_reading_a_document_returns_the_main_file_as_source(
    client: AsyncClient, you: User, project: Project
):
    created = await client.post(
        f"/v1/projects/{project.id}/latex",
        json={"name": "paper", "source": "BODY"},
        headers={"X-Dev-User-Id": you.id},
    )
    doc_id = created.json()["id"]

    got = await client.get(
        f"/v1/projects/{project.id}/latex/{doc_id}", headers={"X-Dev-User-Id": you.id}
    )
    assert got.json()["source"] == "BODY"


async def test_patching_source_writes_the_main_file_and_bumps_the_revision(
    client: AsyncClient, you: User, project: Project
):
    created = await client.post(
        f"/v1/projects/{project.id}/latex",
        json={"name": "paper", "source": "old"},
        headers={"X-Dev-User-Id": you.id},
    )
    doc_id = created.json()["id"]
    before = created.json()["revision"]

    patched = await client.patch(
        f"/v1/projects/{project.id}/latex/{doc_id}",
        json={"source": "new"},
        headers={"X-Dev-User-Id": you.id},
    )
    assert patched.json()["source"] == "new"
    assert patched.json()["revision"] > before

    file_read = await client.get(
        f"/v1/projects/{project.id}/latex/{doc_id}/file",
        params={"path": "main.tex"},
        headers={"X-Dev-User-Id": you.id},
    )
    assert file_read.json()["content"] == "new"


async def test_main_path_can_be_repointed_at_another_tex_file(
    client: AsyncClient, you: User, project: Project
):
    created = await client.post(
        f"/v1/projects/{project.id}/latex",
        json={"name": "paper", "source": "a"},
        headers={"X-Dev-User-Id": you.id},
    )
    doc_id = created.json()["id"]
    await client.put(
        f"/v1/projects/{project.id}/latex/{doc_id}/file",
        params={"path": "other.tex"},
        json={"content": "b"},
        headers={"X-Dev-User-Id": you.id},
    )

    resp = await client.patch(
        f"/v1/projects/{project.id}/latex/{doc_id}",
        json={"main_path": "other.tex"},
        headers={"X-Dev-User-Id": you.id},
    )
    assert resp.status_code == 200
    assert resp.json()["main_path"] == "other.tex"
    assert resp.json()["source"] == "b"


async def test_repointing_main_path_bumps_the_revision(
    client: AsyncClient, you: User, project: Project
):
    """`revision` is the staleness signal plan 4's client compares without
    recomputing any hash -- repointing main_path changes what compiles, so a
    PATCH that changes it and leaves revision behind would read as fresh."""
    created = await client.post(
        f"/v1/projects/{project.id}/latex",
        json={"name": "paper", "source": "a"},
        headers={"X-Dev-User-Id": you.id},
    )
    doc_id = created.json()["id"]
    await client.put(
        f"/v1/projects/{project.id}/latex/{doc_id}/file",
        params={"path": "other.tex"},
        json={"content": "b"},
        headers={"X-Dev-User-Id": you.id},
    )
    before = (
        await client.get(
            f"/v1/projects/{project.id}/latex/{doc_id}", headers={"X-Dev-User-Id": you.id}
        )
    ).json()["revision"]

    resp = await client.patch(
        f"/v1/projects/{project.id}/latex/{doc_id}",
        json={"main_path": "other.tex"},
        headers={"X-Dev-User-Id": you.id},
    )
    assert resp.status_code == 200
    assert resp.json()["revision"] == before + 1


async def test_changing_the_engine_bumps_the_revision(
    client: AsyncClient, you: User, project: Project
):
    created = await client.post(
        f"/v1/projects/{project.id}/latex",
        json={"name": "paper", "source": "a"},
        headers={"X-Dev-User-Id": you.id},
    )
    doc_id = created.json()["id"]
    before = created.json()["revision"]
    assert created.json()["engine"] == "pdflatex"

    resp = await client.patch(
        f"/v1/projects/{project.id}/latex/{doc_id}",
        json={"engine": "xelatex"},
        headers={"X-Dev-User-Id": you.id},
    )
    assert resp.status_code == 200
    assert resp.json()["revision"] == before + 1

    # Setting it to its EXISTING value is not a change -- must not bump.
    unchanged_revision = resp.json()["revision"]
    same = await client.patch(
        f"/v1/projects/{project.id}/latex/{doc_id}",
        json={"engine": "xelatex"},
        headers={"X-Dev-User-Id": you.id},
    )
    assert same.status_code == 200
    assert same.json()["revision"] == unchanged_revision


async def test_main_path_pointing_at_a_missing_file_is_refused(
    client: AsyncClient, you: User, project: Project
):
    created = await client.post(
        f"/v1/projects/{project.id}/latex",
        json={"name": "paper", "source": "a"},
        headers={"X-Dev-User-Id": you.id},
    )
    doc_id = created.json()["id"]

    resp = await client.patch(
        f"/v1/projects/{project.id}/latex/{doc_id}",
        json={"main_path": "ghost.tex"},
        headers={"X-Dev-User-Id": you.id},
    )
    assert resp.status_code == 422


async def test_main_path_pointing_at_a_non_tex_file_is_refused(
    client: AsyncClient, you: User, project: Project
):
    created = await client.post(
        f"/v1/projects/{project.id}/latex",
        json={"name": "paper", "source": "a"},
        headers={"X-Dev-User-Id": you.id},
    )
    doc_id = created.json()["id"]
    await client.put(
        f"/v1/projects/{project.id}/latex/{doc_id}/file",
        params={"path": "refs.bib"},
        json={"content": "@book{}"},
        headers={"X-Dev-User-Id": you.id},
    )

    resp = await client.patch(
        f"/v1/projects/{project.id}/latex/{doc_id}",
        json={"main_path": "refs.bib"},
        headers={"X-Dev-User-Id": you.id},
    )
    assert resp.status_code == 422


async def test_compile_sends_the_main_file_content_not_the_dropped_column(
    client: AsyncClient, you: User, project: Project
):
    """The tree is read fresh inside the compile route (via `list_files` +
    `read_file`) rather than from any stale in-memory value, or a fixed bug
    reappears as a stale compile."""
    created = await client.post(
        f"/v1/projects/{project.id}/latex",
        json={"name": "paper", "source": "FROM-TREE"},
        headers={"X-Dev-User-Id": you.id},
    )
    doc_id = created.json()["id"]

    with patch(
        "app.api.v1.latex.compile_tree",
        new=AsyncMock(
            return_value=CompileResult(
                ok=True, log="", pdf=b"%PDF", synctex_gz=None, root="/tmp/rx-3"
            )
        ),
    ) as mock:
        resp = await client.post(
            f"/v1/projects/{project.id}/latex/{doc_id}/compile",
            headers={"X-Dev-User-Id": you.id},
        )

    assert resp.status_code == 200
    entries, engine, main_path = mock.await_args.args
    assert entries == [("main.tex", b"FROM-TREE")]
    assert engine == "pdflatex"
    assert main_path == "main.tex"
    assert resp.json()["revision"] == created.json()["revision"]


async def test_compiling_a_document_with_a_chapter_sends_both_files_to_compile_tree(
    client: AsyncClient, you: User, project: Project
):
    """The regression this whole plan exists to fix: before the route read
    the tree, only the main file's bytes ever reached the compiler, so an
    `\\input{sec}` in a real multi-file project failed with `! LaTeX Error:
    File 'sec.tex' not found.` -- the chapter was on disk in Postgres but
    never left the backend process."""
    created = await client.post(
        f"/v1/projects/{project.id}/latex",
        json={"name": "paper", "source": "\\input{chapters/intro}"},
        headers={"X-Dev-User-Id": you.id},
    )
    doc_id = created.json()["id"]
    await client.put(
        f"/v1/projects/{project.id}/latex/{doc_id}/file",
        params={"path": "chapters/intro.tex"},
        json={"content": "Chapter body."},
        headers={"X-Dev-User-Id": you.id},
    )

    with patch(
        "app.api.v1.latex.compile_tree",
        new=AsyncMock(
            return_value=CompileResult(
                ok=True, log="", pdf=b"%PDF", synctex_gz=b"gz", root="/tmp/rx-4"
            )
        ),
    ) as mock:
        resp = await client.post(
            f"/v1/projects/{project.id}/latex/{doc_id}/compile",
            headers={"X-Dev-User-Id": you.id},
        )

    assert resp.status_code == 200
    entries, engine, main_path = mock.await_args.args
    assert set(entries) == {
        ("main.tex", b"\\input{chapters/intro}"),
        ("chapters/intro.tex", b"Chapter body."),
    }
    assert engine == "pdflatex"
    assert main_path == "main.tex"


async def test_editing_a_chapter_changes_the_tree_hash_and_triggers_a_real_recompile(
    client: AsyncClient, you: User, project: Project
):
    """The regression that matters most: `source_hash` over the main file
    alone would return a stale PDF forever after editing a chapter, because
    the main file's bytes never changed. `tree_hash` covers every file, so
    the SAME main file with a DIFFERENT chapter must be a cache MISS -- a
    second real compile, not a lookup of the first PDF."""
    created = await client.post(
        f"/v1/projects/{project.id}/latex",
        json={"name": "paper", "source": "\\input{chapters/intro}"},
        headers={"X-Dev-User-Id": you.id},
    )
    doc_id = created.json()["id"]
    await client.put(
        f"/v1/projects/{project.id}/latex/{doc_id}/file",
        params={"path": "chapters/intro.tex"},
        json={"content": "Version one."},
        headers={"X-Dev-User-Id": you.id},
    )

    with (
        patch("app.api.v1.latex.cache", LatexCache(max_entries=4, max_bytes=10_000)),
        patch(
            "app.api.v1.latex.compile_tree",
            new=AsyncMock(
                return_value=CompileResult(
                    ok=True, log="", pdf=b"%PDF-v1", synctex_gz=b"gz", root="/tmp/rx-5"
                )
            ),
        ) as mock,
    ):
        first = await client.post(
            f"/v1/projects/{project.id}/latex/{doc_id}/compile",
            headers={"X-Dev-User-Id": you.id},
        )

        await client.put(
            f"/v1/projects/{project.id}/latex/{doc_id}/file",
            params={"path": "chapters/intro.tex"},
            json={"content": "Version two -- the main file never changed."},
            headers={"X-Dev-User-Id": you.id},
        )
        mock.return_value = CompileResult(
            ok=True, log="", pdf=b"%PDF-v2", synctex_gz=b"gz", root="/tmp/rx-5"
        )
        second = await client.post(
            f"/v1/projects/{project.id}/latex/{doc_id}/compile",
            headers={"X-Dev-User-Id": you.id},
        )

    assert first.json()["pdf_hash"] != second.json()["pdf_hash"]
    # A genuine recompile, not a cache hit that skipped the compiler: called
    # once per POST /compile above.
    assert mock.await_count == 2


async def test_a_binary_file_in_the_tree_is_sent_as_bytes_not_decoded(
    client: AsyncClient, you: User, project: Project
):
    """The tree-reading loop must branch on `is_binary` and read `.blob`,
    not `.content` -- decoding an image as UTF-8 would raise, or worse,
    silently corrupt it if it happened to decode."""
    created = await client.post(
        f"/v1/projects/{project.id}/latex",
        json={"name": "paper", "source": "\\includegraphics{figure.png}"},
        headers={"X-Dev-User-Id": you.id},
    )
    doc_id = created.json()["id"]
    png_bytes = b"\x89PNG\r\n\x1a\n" + bytes(range(256))
    await client.post(
        f"/v1/projects/{project.id}/latex/{doc_id}/file/binary",
        params={"path": "figure.png"},
        content=png_bytes,
        headers={"X-Dev-User-Id": you.id},
    )

    with patch(
        "app.api.v1.latex.compile_tree",
        new=AsyncMock(
            return_value=CompileResult(
                ok=True, log="", pdf=b"%PDF", synctex_gz=None, root="/tmp/rx-6"
            )
        ),
    ) as mock:
        resp = await client.post(
            f"/v1/projects/{project.id}/latex/{doc_id}/compile",
            headers={"X-Dev-User-Id": you.id},
        )

    assert resp.status_code == 200
    entries, _engine, _main_path = mock.await_args.args
    by_path = dict(entries)
    assert by_path["figure.png"] == png_bytes


async def test_a_patch_with_a_traversal_main_path_is_a_422(
    client: AsyncClient, you: User, project: Project
):
    """`main_path` reaches `normalize_path` through `files.read_file`, which
    raises `InvalidPath` for a traversal attempt. Nothing caught that before
    this fix, so FastAPI turned a plain user error into a 500."""
    created = await client.post(
        f"/v1/projects/{project.id}/latex",
        json={"name": "paper", "source": "a"},
        headers={"X-Dev-User-Id": you.id},
    )
    doc_id = created.json()["id"]

    resp = await client.patch(
        f"/v1/projects/{project.id}/latex/{doc_id}",
        json={"main_path": "../etc/passwd.tex"},
        headers={"X-Dev-User-Id": you.id},
    )
    assert resp.status_code == 422


async def test_repointing_main_path_by_a_denormalized_path_stores_the_normalized_form(
    client: AsyncClient, you: User, project: Project
):
    """`document.main_path` must be stored NORMALIZED, or it silently stops
    matching the row's actual `path` -- which defeats the delete route's
    "is this the main file" guard (compared against `document.main_path`
    expecting it already canonical)."""
    created = await client.post(
        f"/v1/projects/{project.id}/latex",
        json={"name": "paper", "source": "a"},
        headers={"X-Dev-User-Id": you.id},
    )
    doc_id = created.json()["id"]
    await client.put(
        f"/v1/projects/{project.id}/latex/{doc_id}/file",
        params={"path": "other.tex"},
        json={"content": "b"},
        headers={"X-Dev-User-Id": you.id},
    )

    resp = await client.patch(
        f"/v1/projects/{project.id}/latex/{doc_id}",
        json={"main_path": "./other.tex"},
        headers={"X-Dev-User-Id": you.id},
    )
    assert resp.status_code == 200

    got = await client.get(
        f"/v1/projects/{project.id}/latex/{doc_id}", headers={"X-Dev-User-Id": you.id}
    )
    assert got.json()["main_path"] == "other.tex"

    guarded = await client.delete(
        f"/v1/projects/{project.id}/latex/{doc_id}/file",
        params={"path": "other.tex"},
        headers={"X-Dev-User-Id": you.id},
    )
    assert guarded.status_code == 409


async def test_a_patch_carrying_both_main_path_and_source_writes_the_new_main_file(
    client: AsyncClient, you: User, project: Project
):
    """Order matters: `main_path` is applied BEFORE `source`. Reversed, the
    content silently lands in the OLD main file with no error -- this test
    is what fails if that order regresses."""
    created = await client.post(
        f"/v1/projects/{project.id}/latex",
        json={"name": "paper", "source": "ORIGINAL"},
        headers={"X-Dev-User-Id": you.id},
    )
    doc_id = created.json()["id"]
    await client.put(
        f"/v1/projects/{project.id}/latex/{doc_id}/file",
        params={"path": "other.tex"},
        json={"content": "stub"},
        headers={"X-Dev-User-Id": you.id},
    )

    resp = await client.patch(
        f"/v1/projects/{project.id}/latex/{doc_id}",
        json={"main_path": "other.tex", "source": "Z"},
        headers={"X-Dev-User-Id": you.id},
    )
    assert resp.status_code == 200

    other = await client.get(
        f"/v1/projects/{project.id}/latex/{doc_id}/file",
        params={"path": "other.tex"},
        headers={"X-Dev-User-Id": you.id},
    )
    assert other.json()["content"] == "Z"

    main = await client.get(
        f"/v1/projects/{project.id}/latex/{doc_id}/file",
        params={"path": "main.tex"},
        headers={"X-Dev-User-Id": you.id},
    )
    assert main.json()["content"] == "ORIGINAL"


async def test_creating_a_document_that_would_exceed_the_project_quota_is_a_413_and_creates_nothing(
    client: AsyncClient, you: User, project: Project, monkeypatch
):
    """`files.write_text` can raise `QuotaExceeded` on create -- the tree
    write happens after the document row exists. Before this fix that
    surfaced as an unhandled 500, and worse, left an orphan document with
    an empty tree because the row was already committed. Now the row and
    the tree write share one transaction, so a quota failure leaves nothing
    behind."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "latex_project_max_bytes", 4)

    resp = await client.post(
        f"/v1/projects/{project.id}/latex",
        json={"name": "paper", "source": "way too big for the cap"},
        headers={"X-Dev-User-Id": you.id},
    )
    assert resp.status_code == 413

    listed = await client.get(f"/v1/projects/{project.id}/latex", headers={"X-Dev-User-Id": you.id})
    assert listed.json() == []
