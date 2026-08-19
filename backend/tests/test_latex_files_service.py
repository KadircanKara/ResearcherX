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
    await svc.write_text(db_session, document.id, "main.tex", "new")
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
    row = await svc.write_binary(db_session, document.id, "f.dat", b"\x00\x01")
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
    row = await svc.write_text(db_session, document.id, "f.dat", "café")
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

    row = await svc.write_text(db_session, document.id, "a.tex", "y" * 20)
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
