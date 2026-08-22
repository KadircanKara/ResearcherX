"""The file tree behind a document: writes, quota, revision, and hashing."""

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import LatexDocument, LatexFile, Project, User
from app.db.seed import seed_users
from app.services import latex_files_service as svc
from app.services.latex_paths import InvalidPath


@pytest_asyncio.fixture
async def document(db_session: AsyncSession) -> LatexDocument:
    await seed_users(db_session)
    user = (
        await db_session.execute(select(User).where(User.email == "you@researcherx.dev"))
    ).scalar_one()
    project = Project(owner_id=user.id, title="Tree Test", topic_keywords=[])
    db_session.add(project)
    await db_session.flush()
    doc = LatexDocument(project_id=project.id, name="paper")
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)
    return doc


async def test_writing_a_text_file_stores_its_utf8_byte_length(
    db_session: AsyncSession, document: LatexDocument
):
    row = await svc.write_text(db_session, document.id, "main.tex", "café")
    await db_session.commit()

    assert row.path == "main.tex"
    assert row.is_binary is False
    assert row.content == "café"
    assert row.size_bytes == 5  # 4 chars, 5 UTF-8 bytes


async def test_writing_a_file_bumps_the_document_revision(
    db_session: AsyncSession, document: LatexDocument
):
    before = document.revision
    await svc.write_text(db_session, document.id, "main.tex", "a")
    await db_session.commit()
    await db_session.refresh(document)

    assert document.revision == before + 1


async def test_deleting_a_file_also_bumps_the_revision(
    db_session: AsyncSession, document: LatexDocument
):
    await svc.write_text(db_session, document.id, "main.tex", "a")
    await db_session.commit()
    await db_session.refresh(document)
    before = document.revision

    assert await svc.delete_file(db_session, document.id, "main.tex") is True
    await db_session.commit()
    await db_session.refresh(document)

    assert document.revision == before + 1


async def test_deleting_a_file_that_is_not_there_reports_false_and_does_not_bump(
    db_session: AsyncSession, document: LatexDocument
):
    before = document.revision
    assert await svc.delete_file(db_session, document.id, "nope.tex") is False
    await db_session.commit()
    await db_session.refresh(document)

    assert document.revision == before


async def test_writing_over_a_file_replaces_it_rather_than_adding_a_second_row(
    db_session: AsyncSession, document: LatexDocument
):
    await svc.write_text(db_session, document.id, "main.tex", "old")
    await db_session.commit()
    await svc.write_text(db_session, document.id, "main.tex", "new", if_exists="replace")
    await db_session.commit()

    rows = (
        (await db_session.execute(select(LatexFile).where(LatexFile.document_id == document.id)))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].content == "new"


async def test_a_binary_write_stores_the_blob_and_leaves_content_null(
    db_session: AsyncSession, document: LatexDocument
):
    row = await svc.write_binary(db_session, document.id, "figures/f.png", b"\x89PNG")
    await db_session.commit()

    assert row.is_binary is True
    assert row.blob == b"\x89PNG"
    assert row.content is None
    assert row.size_bytes == 4


async def test_a_bad_path_is_rejected_before_anything_is_written(
    db_session: AsyncSession, document: LatexDocument
):
    with pytest.raises(InvalidPath):
        await svc.write_text(db_session, document.id, "../../etc/passwd", "x")
    await db_session.rollback()

    assert (await db_session.execute(select(LatexFile))).scalars().all() == []


async def test_overwriting_a_text_file_with_binary_clears_the_text_column(
    db_session: AsyncSession, document: LatexDocument
):
    await svc.write_text(db_session, document.id, "f.dat", "text")
    await db_session.commit()
    row = await svc.write_binary(db_session, document.id, "f.dat", b"\x00\x01", if_exists="replace")
    await db_session.commit()

    assert row.is_binary is True
    assert row.content is None
    assert row.blob == b"\x00\x01"
    assert row.size_bytes == 2


async def test_overwriting_a_binary_file_with_text_clears_the_blob_column(
    db_session: AsyncSession, document: LatexDocument
):
    await svc.write_binary(db_session, document.id, "f.dat", b"\x00\x01")
    await db_session.commit()
    row = await svc.write_text(db_session, document.id, "f.dat", "café", if_exists="replace")
    await db_session.commit()

    assert row.is_binary is False
    assert row.blob is None
    assert row.content == "café"
    assert row.size_bytes == 5


async def test_a_file_over_the_per_file_cap_is_refused(
    db_session: AsyncSession, document: LatexDocument, monkeypatch
):
    monkeypatch.setattr(settings, "latex_file_max_bytes", 10)
    with pytest.raises(svc.QuotaExceeded):
        await svc.write_text(db_session, document.id, "main.tex", "x" * 11)


async def test_a_write_that_would_cross_the_project_cap_is_refused(
    db_session: AsyncSession, document: LatexDocument, monkeypatch
):
    monkeypatch.setattr(settings, "latex_project_max_bytes", 20)
    await svc.write_text(db_session, document.id, "a.tex", "x" * 15)
    await db_session.commit()

    with pytest.raises(svc.QuotaExceeded):
        await svc.write_text(db_session, document.id, "b.tex", "y" * 10)


async def test_overwriting_a_file_does_not_count_its_old_size_against_the_cap(
    db_session: AsyncSession, document: LatexDocument, monkeypatch
):
    """Otherwise a project sitting at the cap could never be edited again,
    only deleted."""
    monkeypatch.setattr(settings, "latex_project_max_bytes", 20)
    await svc.write_text(db_session, document.id, "a.tex", "x" * 20)
    await db_session.commit()

    row = await svc.write_text(db_session, document.id, "a.tex", "y" * 20, if_exists="replace")
    await db_session.commit()

    assert row.content == "y" * 20


async def test_exceeding_the_file_count_cap_is_refused(
    db_session: AsyncSession, document: LatexDocument, monkeypatch
):
    monkeypatch.setattr(settings, "latex_max_files", 2)
    await svc.write_text(db_session, document.id, "a.tex", "a")
    await svc.write_text(db_session, document.id, "b.tex", "b")
    await db_session.commit()

    with pytest.raises(svc.TooManyFiles):
        await svc.write_text(db_session, document.id, "c.tex", "c")


async def test_a_path_colliding_only_by_case_is_refused(
    db_session: AsyncSession, document: LatexDocument
):
    await svc.write_text(db_session, document.id, "figures/fig.tex", "a")
    await db_session.commit()

    with pytest.raises(svc.PathCollision):
        await svc.write_text(db_session, document.id, "Figures/Fig.tex", "b")


async def test_renaming_moves_the_content_and_frees_the_old_path(
    db_session: AsyncSession, document: LatexDocument
):
    await svc.write_text(db_session, document.id, "a.tex", "body")
    await db_session.commit()

    moved = await svc.rename_file(db_session, document.id, "a.tex", "chapters/b.tex")
    await db_session.commit()

    assert moved.path == "chapters/b.tex"
    assert moved.content == "body"
    assert await svc.read_file(db_session, document.id, "a.tex") is None


async def test_renaming_onto_an_occupied_path_is_refused(
    db_session: AsyncSession, document: LatexDocument
):
    await svc.write_text(db_session, document.id, "a.tex", "a")
    await svc.write_text(db_session, document.id, "b.tex", "b")
    await db_session.commit()

    with pytest.raises(svc.PathCollision):
        await svc.rename_file(db_session, document.id, "a.tex", "b.tex")


async def test_renaming_a_missing_file_is_refused(
    db_session: AsyncSession, document: LatexDocument
):
    with pytest.raises(svc.FileNotFound):
        await svc.rename_file(db_session, document.id, "ghost.tex", "b.tex")


async def test_listing_returns_paths_in_sorted_order(
    db_session: AsyncSession, document: LatexDocument
):
    await svc.write_text(db_session, document.id, "z.tex", "z")
    await svc.write_text(db_session, document.id, "a.tex", "a")
    await svc.write_text(db_session, document.id, "m/n.tex", "n")
    await db_session.commit()

    assert [f.path for f in await svc.list_files(db_session, document.id)] == [
        "a.tex",
        "m/n.tex",
        "z.tex",
    ]


async def test_used_bytes_sums_the_tree(db_session: AsyncSession, document: LatexDocument):
    await svc.write_text(db_session, document.id, "a.tex", "12345")
    await svc.write_binary(db_session, document.id, "b.png", b"123")
    await db_session.commit()

    assert await svc.used_bytes(db_session, document.id) == 8


async def test_used_bytes_of_an_empty_document_is_zero(
    db_session: AsyncSession, document: LatexDocument
):
    assert await svc.used_bytes(db_session, document.id) == 0


def test_tree_hash_is_stable_across_entry_order():
    a = svc.tree_hash([("a.tex", b"1"), ("b.tex", b"2")], "pdflatex", "a.tex")
    b = svc.tree_hash([("b.tex", b"2"), ("a.tex", b"1")], "pdflatex", "a.tex")
    assert a == b


def test_tree_hash_changes_when_content_changes():
    a = svc.tree_hash([("a.tex", b"1")], "pdflatex", "a.tex")
    b = svc.tree_hash([("a.tex", b"2")], "pdflatex", "a.tex")
    assert a != b


def test_tree_hash_changes_when_a_path_changes():
    a = svc.tree_hash([("a.tex", b"1")], "pdflatex", "a.tex")
    b = svc.tree_hash([("c.tex", b"1")], "pdflatex", "a.tex")
    assert a != b


def test_tree_hash_changes_when_the_engine_changes():
    a = svc.tree_hash([("a.tex", b"1")], "pdflatex", "a.tex")
    b = svc.tree_hash([("a.tex", b"1")], "xelatex", "a.tex")
    assert a != b


def test_tree_hash_changes_when_the_main_file_changes():
    entries = [("a.tex", b"1"), ("b.tex", b"1")]
    assert svc.tree_hash(entries, "pdflatex", "a.tex") != svc.tree_hash(
        entries, "pdflatex", "b.tex"
    )


def test_tree_hash_cannot_be_forged_by_moving_bytes_between_path_and_content():
    """Length-prefix-free concatenation would let 'ab' + '' collide with
    'a' + 'b'. The separator is what stops it."""
    a = svc.tree_hash([("ab.tex", b"")], "pdflatex", "m.tex")
    b = svc.tree_hash([("a.tex", b"b")], "pdflatex", "m.tex")
    assert a != b


async def test_creating_a_file_at_a_taken_path_refuses_instead_of_blanking_it(
    db_session: AsyncSession, document: LatexDocument
):
    # The live data-loss bug this whole feature exists to close: the "New
    # file" box PUT an empty string, and the write silently replaced the
    # file's contents with it.
    doc_id = document.id  # captured before the rollback below expires it
    await svc.write_text(db_session, doc_id, "main.tex", "\\documentclass{article}")
    await db_session.commit()

    with pytest.raises(svc.PathCollision) as excinfo:
        await svc.write_text(db_session, doc_id, "main.tex", "")

    assert excinfo.value.suggestion == "main (1).tex"
    await db_session.rollback()
    row = await svc.read_file(db_session, doc_id, "main.tex")
    assert row.content == "\\documentclass{article}"


async def test_an_explicit_replace_still_overwrites(
    db_session: AsyncSession, document: LatexDocument
):
    # Autosave's path. It knows the file exists and means to replace it.
    await svc.write_text(db_session, document.id, "main.tex", "one")
    row = await svc.write_text(db_session, document.id, "main.tex", "two", if_exists="replace")
    await db_session.commit()
    assert row.content == "two"


async def test_a_binary_upload_refuses_a_taken_path(
    db_session: AsyncSession, document: LatexDocument
):
    await svc.write_binary(db_session, document.id, "figures/plot.png", b"\x89PNG")
    await db_session.commit()

    with pytest.raises(svc.PathCollision) as excinfo:
        await svc.write_binary(db_session, document.id, "figures/plot.png", b"\x89PNG2")

    assert excinfo.value.suggestion == "figures/plot (1).png"


async def test_a_case_only_collision_now_carries_a_suggestion(
    db_session: AsyncSession, document: LatexDocument
):
    # Previously a dead-end 409. The user is now offered a way forward.
    await svc.write_binary(db_session, document.id, "figures/fig.png", b"\x89PNG")
    await db_session.commit()

    with pytest.raises(svc.PathCollision) as excinfo:
        await svc.write_binary(db_session, document.id, "figures/Fig.PNG", b"\x89PNG")

    assert excinfo.value.existing == "figures/fig.png"
    assert excinfo.value.suggestion == "figures/Fig (1).PNG"


async def test_a_rename_onto_a_taken_path_carries_a_suggestion(
    db_session: AsyncSession, document: LatexDocument
):
    await svc.write_text(db_session, document.id, "a.tex", "a")
    await svc.write_text(db_session, document.id, "b.tex", "b")
    await db_session.commit()

    with pytest.raises(svc.PathCollision) as excinfo:
        await svc.rename_file(db_session, document.id, "a.tex", "b.tex")

    assert excinfo.value.suggestion == "b (1).tex"


async def test_bulk_merge_adds_to_an_existing_tree(
    db_session: AsyncSession, document: LatexDocument
):
    await svc.write_text(db_session, document.id, "main.tex", "existing")
    await db_session.commit()

    count = await svc.bulk_merge(db_session, document.id, [("chapters/intro.tex", b"intro", False)])
    await db_session.commit()

    assert count == 1
    paths = [f.path for f in await svc.list_files(db_session, document.id)]
    assert paths == ["chapters/intro.tex", "main.tex"]


async def test_bulk_merge_bumps_the_revision_exactly_once(
    db_session: AsyncSession, document: LatexDocument
):
    # A merge is ONE change. Bumping per file would make every merged file
    # look like a separate edit to the compile-staleness check.
    await svc.write_text(db_session, document.id, "main.tex", "x")
    await db_session.commit()
    before = (await db_session.get(LatexDocument, document.id)).revision

    await svc.bulk_merge(
        db_session,
        document.id,
        [("a.tex", b"a", False), ("b.tex", b"b", False), ("c.tex", b"c", False)],
    )
    await db_session.commit()

    after = (await db_session.get(LatexDocument, document.id)).revision
    assert after == before + 1


async def test_bulk_merge_refuses_a_path_that_is_already_taken(
    db_session: AsyncSession, document: LatexDocument
):
    # The caller resolves collisions BEFORE calling. Reaching here with a
    # taken path is a bug in the caller, and it must not overwrite.
    await svc.write_text(db_session, document.id, "main.tex", "existing")
    await db_session.commit()

    with pytest.raises(svc.PathCollision):
        await svc.bulk_merge(db_session, document.id, [("main.tex", b"new", False)])


async def test_bulk_merge_counts_the_existing_tree_against_the_byte_cap(
    db_session: AsyncSession, document: LatexDocument, monkeypatch
):
    monkeypatch.setattr(settings, "latex_project_max_bytes", 100)
    await svc.write_text(db_session, document.id, "main.tex", "x" * 60)
    await db_session.commit()

    with pytest.raises(svc.QuotaExceeded):
        await svc.bulk_merge(db_session, document.id, [("big.tex", b"y" * 60, False)])


async def test_renaming_a_directory_moves_every_file_beneath_it(
    db_session: AsyncSession, document: LatexDocument
):
    await svc.write_text(db_session, document.id, "chapters/intro.tex", "one")
    await svc.write_text(db_session, document.id, "chapters/deep/two.tex", "two")
    await svc.write_text(db_session, document.id, "main.tex", "root")
    await db_session.commit()

    moved = await svc.rename_dir(db_session, document.id, "chapters", "parts")
    await db_session.commit()

    assert moved == 2
    assert [f.path for f in await svc.list_files(db_session, document.id)] == [
        "main.tex",
        "parts/deep/two.tex",
        "parts/intro.tex",
    ]


async def test_renaming_a_directory_bumps_the_revision_exactly_once(
    db_session: AsyncSession, document: LatexDocument
):
    # A move is ONE change. Bumping per file would make a three-file
    # directory look like three separate edits to the staleness check.
    await svc.write_text(db_session, document.id, "figs/a.tex", "a")
    await svc.write_text(db_session, document.id, "figs/b.tex", "b")
    await svc.write_text(db_session, document.id, "figs/c.tex", "c")
    await db_session.commit()
    before = (await db_session.get(LatexDocument, document.id)).revision

    await svc.rename_dir(db_session, document.id, "figs", "images")
    await db_session.commit()

    after = (await db_session.get(LatexDocument, document.id)).revision
    assert after == before + 1


async def test_a_directory_rename_moves_nothing_when_any_file_would_collide(
    db_session: AsyncSession, document: LatexDocument
):
    # All-or-nothing: a collision on the SECOND file must leave the first
    # where it was. A half-moved tree is a project silently missing the file
    # its \input names.
    doc_id = document.id
    await svc.write_text(db_session, doc_id, "a/one.tex", "one")
    await svc.write_text(db_session, doc_id, "a/two.tex", "two")
    await svc.write_text(db_session, doc_id, "b/two.tex", "occupied")
    await db_session.commit()

    with pytest.raises(svc.PathCollision):
        await svc.rename_dir(db_session, doc_id, "a", "b")

    await db_session.rollback()
    assert [f.path for f in await svc.list_files(db_session, doc_id)] == [
        "a/one.tex",
        "a/two.tex",
        "b/two.tex",
    ]


async def test_a_directory_cannot_be_moved_inside_itself(
    db_session: AsyncSession, document: LatexDocument
):
    # The destination prefix would slide as the source is consumed.
    await svc.write_text(db_session, document.id, "src/main.tex", "x")
    await db_session.commit()

    with pytest.raises(InvalidPath):
        await svc.rename_dir(db_session, document.id, "src", "src/nested")


async def test_renaming_a_directory_that_holds_no_files_is_a_miss(
    db_session: AsyncSession, document: LatexDocument
):
    await svc.write_text(db_session, document.id, "main.tex", "x")
    await db_session.commit()

    with pytest.raises(svc.FileNotFound):
        await svc.rename_dir(db_session, document.id, "nope", "elsewhere")


async def test_a_directory_rename_does_not_touch_a_file_whose_name_merely_starts_the_same(
    db_session: AsyncSession, document: LatexDocument
):
    # `chapters-old.tex` shares the prefix `chapters` but is not INSIDE it;
    # matching on the bare prefix rather than `chapters/` would move it.
    await svc.write_text(db_session, document.id, "chapters/intro.tex", "in")
    await svc.write_text(db_session, document.id, "chapters-old.tex", "out")
    await db_session.commit()

    await svc.rename_dir(db_session, document.id, "chapters", "parts")
    await db_session.commit()

    assert [f.path for f in await svc.list_files(db_session, document.id)] == [
        "chapters-old.tex",
        "parts/intro.tex",
    ]
