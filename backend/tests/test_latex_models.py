"""The document row. Source is the only durable artifact; the PDF is derived."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LatexDocument, Project, User
from app.db.seed import seed_users


async def _project(db: AsyncSession) -> Project:
    await seed_users(db)
    user = (await db.execute(select(User).where(User.email == "you@researcherx.dev"))).scalar_one()
    project = Project(owner_id=user.id, title="LaTeX Test", topic_keywords=[])
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return project


async def test_a_document_persists_its_source_and_defaults_to_pdflatex(db_session: AsyncSession):
    project = await _project(db_session)
    doc = LatexDocument(project_id=project.id, name="main.tex", source="\\documentclass{article}")
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)

    assert doc.engine == "pdflatex"
    assert doc.source == "\\documentclass{article}"
    assert doc.created_at is not None
    assert doc.updated_at is not None


async def test_documents_are_scoped_to_their_project(db_session: AsyncSession):
    project = await _project(db_session)
    db_session.add(LatexDocument(project_id=project.id, name="a.tex", source=""))
    db_session.add(LatexDocument(project_id=project.id, name="b.tex", source=""))
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
