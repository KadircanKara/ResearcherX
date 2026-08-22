import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.db.models import User, Project, ProjectMember, Role


async def test_project_and_member_persist(db_session):
    u = User(email="o@x.io", name="O")
    db_session.add(u)
    await db_session.flush()
    p = Project(owner_id=u.id, title="P", description="d", topic_keywords=["a", "b"])
    db_session.add(p)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=p.id, user_id=u.id, role=Role.OWNER))
    await db_session.commit()
    got = (await db_session.execute(select(Project).where(Project.id == p.id))).scalar_one()
    assert got.topic_keywords == ["a", "b"]
    m = (await db_session.execute(select(ProjectMember))).scalar_one()
    assert m.role == "owner"  # plain str after round-trip


async def test_member_unique(db_session):
    u = User(email="o2@x.io", name="O")
    db_session.add(u)
    await db_session.flush()
    p = Project(owner_id=u.id, title="P")
    db_session.add(p)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=p.id, user_id=u.id, role=Role.OWNER))
    await db_session.commit()
    db_session.add(ProjectMember(project_id=p.id, user_id=u.id, role=Role.MEMBER))
    with pytest.raises(IntegrityError):
        await db_session.commit()
