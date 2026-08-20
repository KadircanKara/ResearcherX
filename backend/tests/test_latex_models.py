"""The document row. The `latex_files` tree is the only durable artifact; the
PDF is derived."""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import column, delete, insert, select, table
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LatexDocument, LatexFile, Project, User
from app.db.seed import seed_users


async def _project(db: AsyncSession) -> Project:
    await seed_users(db)
    user = (await db.execute(select(User).where(User.email == "you@researcherx.dev"))).scalar_one()
    project = Project(owner_id=user.id, title="LaTeX Test", topic_keywords=[])
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return project


async def test_a_document_defaults_to_pdflatex(db_session: AsyncSession):
    project = await _project(db_session)
    doc = LatexDocument(project_id=project.id, name="main.tex")
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)

    assert doc.engine == "pdflatex"
    assert doc.created_at is not None
    assert doc.updated_at is not None


async def test_documents_are_scoped_to_their_project(db_session: AsyncSession):
    project = await _project(db_session)
    db_session.add(LatexDocument(project_id=project.id, name="a.tex"))
    db_session.add(LatexDocument(project_id=project.id, name="b.tex"))
    await db_session.commit()

    rows = (
        (
            await db_session.execute(
                select(LatexDocument).where(LatexDocument.project_id == project.id)
            )
        )
        .scalars()
        .all()
    )

    assert {r.name for r in rows} == {"a.tex", "b.tex"}


async def test_a_new_document_defaults_to_main_tex_at_revision_one(db_session: AsyncSession):
    project = await _project(db_session)
    doc = LatexDocument(project_id=project.id, name="paper")
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)

    assert doc.main_path == "main.tex"
    assert doc.revision == 1


async def test_a_row_inserted_without_main_path_or_revision_gets_the_column_server_defaults(
    db_session: AsyncSession,
):
    """The ORM test above only exercises the Python-side `default=`. This
    inserts through a bare Core `table()` construct that carries none of the
    mapped Column's client-side defaults, so `main.tex`/`1` can only come
    from the `server_default` the migration wrote into the schema."""
    project = await _project(db_session)
    doc_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    latex_documents = table(
        "latex_documents",
        column("id"),
        column("project_id"),
        column("name"),
        column("engine"),
        column("created_at"),
        column("updated_at"),
    )

    await db_session.execute(
        insert(latex_documents).values(
            id=doc_id,
            project_id=project.id,
            name="raw.tex",
            engine="pdflatex",
            created_at=now,
            updated_at=now,
        )
    )
    await db_session.commit()

    row = (
        await db_session.execute(select(LatexDocument).where(LatexDocument.id == doc_id))
    ).scalar_one()
    assert row.main_path == "main.tex"
    assert row.revision == 1


async def test_a_text_file_persists_against_its_document(db_session: AsyncSession):
    project = await _project(db_session)
    doc = LatexDocument(project_id=project.id, name="paper")
    db_session.add(doc)
    await db_session.flush()
    db_session.add(
        LatexFile(
            document_id=doc.id,
            path="chapters/intro.tex",
            is_binary=False,
            content="\\section{Intro}",
            size_bytes=15,
        )
    )
    await db_session.commit()

    rows = (
        (await db_session.execute(select(LatexFile).where(LatexFile.document_id == doc.id)))
        .scalars()
        .all()
    )
    assert [r.path for r in rows] == ["chapters/intro.tex"]
    assert rows[0].blob is None
    assert rows[0].created_at is not None


async def test_a_binary_file_persists_its_blob(db_session: AsyncSession):
    project = await _project(db_session)
    doc = LatexDocument(project_id=project.id, name="paper")
    db_session.add(doc)
    await db_session.flush()
    db_session.add(
        LatexFile(
            document_id=doc.id,
            path="figures/fig1.png",
            is_binary=True,
            blob=b"\x89PNG\r\n\x1a\n",
            size_bytes=8,
        )
    )
    await db_session.commit()

    row = (
        await db_session.execute(select(LatexFile).where(LatexFile.path == "figures/fig1.png"))
    ).scalar_one()
    assert row.blob == b"\x89PNG\r\n\x1a\n"
    assert row.content is None


async def test_two_files_cannot_share_a_path_within_one_document(db_session: AsyncSession):
    project = await _project(db_session)
    doc = LatexDocument(project_id=project.id, name="paper")
    db_session.add(doc)
    await db_session.flush()
    db_session.add(
        LatexFile(document_id=doc.id, path="main.tex", is_binary=False, content="a", size_bytes=1)
    )
    db_session.add(
        LatexFile(document_id=doc.id, path="main.tex", is_binary=False, content="b", size_bytes=1)
    )

    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_the_same_path_in_two_documents_is_fine(db_session: AsyncSession):
    project = await _project(db_session)
    a = LatexDocument(project_id=project.id, name="a")
    b = LatexDocument(project_id=project.id, name="b")
    db_session.add_all([a, b])
    await db_session.flush()
    db_session.add(
        LatexFile(document_id=a.id, path="main.tex", is_binary=False, content="a", size_bytes=1)
    )
    db_session.add(
        LatexFile(document_id=b.id, path="main.tex", is_binary=False, content="b", size_bytes=1)
    )
    await db_session.commit()

    rows = (await db_session.execute(select(LatexFile))).scalars().all()
    assert len(rows) == 2


async def test_a_row_claiming_to_be_text_while_holding_a_blob_is_refused(db_session: AsyncSession):
    """The CHECK is what stops the two content columns drifting into a third
    state no reader handles."""
    project = await _project(db_session)
    doc = LatexDocument(project_id=project.id, name="paper")
    db_session.add(doc)
    await db_session.flush()
    db_session.add(
        LatexFile(
            document_id=doc.id,
            path="main.tex",
            is_binary=False,
            content="a",
            blob=b"also",
            size_bytes=1,
        )
    )

    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_a_row_with_neither_content_nor_blob_is_refused(db_session: AsyncSession):
    project = await _project(db_session)
    doc = LatexDocument(project_id=project.id, name="paper")
    db_session.add(doc)
    await db_session.flush()
    db_session.add(LatexFile(document_id=doc.id, path="main.tex", is_binary=False, size_bytes=0))

    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_deleting_a_document_deletes_its_files(db_session: AsyncSession):
    project = await _project(db_session)
    doc = LatexDocument(project_id=project.id, name="paper")
    db_session.add(doc)
    await db_session.flush()
    db_session.add(
        LatexFile(document_id=doc.id, path="main.tex", is_binary=False, content="a", size_bytes=1)
    )
    await db_session.commit()

    await db_session.execute(delete(LatexDocument).where(LatexDocument.id == doc.id))
    await db_session.commit()

    assert (await db_session.execute(select(LatexFile))).scalars().all() == []
