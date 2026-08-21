"""Merging an archive into an already-open document: collision planning and
the write itself. Uses the same `document` fixture pattern as
`test_latex_files_service.py` because `test_latex_import_api.py`'s fixtures
build a bare `project`, not a `LatexDocument` with a tree to merge into."""

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LatexDocument, Project, User
from app.db.seed import seed_users
from app.services import latex_files_service as files
from app.services import latex_import_service as svc
from app.services.latex_archive import ArchiveEntry
from app.services.latex_paths import MANIFEST_PATH


@pytest_asyncio.fixture
async def document(db_session: AsyncSession) -> LatexDocument:
    await seed_users(db_session)
    user = (
        await db_session.execute(select(User).where(User.email == "you@researcherx.dev"))
    ).scalar_one()
    project = Project(owner_id=user.id, title="Merge Test", topic_keywords=[])
    db_session.add(project)
    await db_session.flush()
    doc = LatexDocument(project_id=project.id, name="paper")
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)
    return doc


async def test_plan_merge_ignores_the_manifest_entry():
    entries = [
        ArchiveEntry(path=MANIFEST_PATH, data=b"{}", is_binary=False),
        ArchiveEntry(path="main.tex", data=b"x", is_binary=False),
    ]
    assert [c.path for c in svc.plan_merge(["main.tex"], entries)] == ["main.tex"]


async def test_merge_archive_applies_the_caller_s_renames(
    db_session: AsyncSession, document: LatexDocument
):
    await files.write_text(db_session, document.id, "main.tex", "existing")
    await db_session.commit()

    count = await svc.merge_archive(
        db_session,
        document_id=document.id,
        entries=[ArchiveEntry(path="main.tex", data=b"incoming", is_binary=False)],
        renames={"main.tex": "main (1).tex"},
    )
    await db_session.commit()

    assert count == 1
    paths = [f.path for f in await files.list_files(db_session, document.id)]
    assert paths == ["main (1).tex", "main.tex"]
    kept = await files.read_file(db_session, document.id, "main.tex")
    assert kept.content == "existing"


async def test_merge_archive_leaves_main_path_and_engine_alone(
    db_session: AsyncSession, document: LatexDocument
):
    document.main_path = "main.tex"
    document.engine = "pdflatex"
    await db_session.commit()

    await svc.merge_archive(
        db_session,
        document_id=document.id,
        entries=[ArchiveEntry(path="extra.tex", data=b"\\usepackage{fontspec}", is_binary=False)],
        renames={},
    )
    await db_session.commit()
    await db_session.refresh(document)

    # Merge adds FILES. The open document keeps compiling what it compiled
    # before -- adopting the archive's engine would change a document-wide
    # setting the user never asked about.
    assert document.main_path == "main.tex"
    assert document.engine == "pdflatex"
