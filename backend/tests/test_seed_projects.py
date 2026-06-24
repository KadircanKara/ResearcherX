from sqlalchemy import func, select

from app.db.models import Project, ProjectMember, Role
from app.db.seed import seed_projects, seed_users


async def test_seed_projects_creates_exactly_three(db_session):
    await seed_users(db_session)
    await db_session.commit()
    await seed_projects(db_session)
    await db_session.commit()
    await seed_projects(db_session)
    await db_session.commit()

    count = (await db_session.execute(select(func.count()).select_from(Project))).scalar_one()
    assert count == 3


async def test_multi_uav_has_three_members_with_you_as_owner(db_session):
    await seed_users(db_session)
    await db_session.commit()
    await seed_projects(db_session)
    await db_session.commit()

    project = (
        await db_session.execute(select(Project).where(Project.title == "Multi-UAV Coordination"))
    ).scalar_one()

    members = (
        (
            await db_session.execute(
                select(ProjectMember).where(ProjectMember.project_id == project.id)
            )
        )
        .scalars()
        .all()
    )

    assert len(members) == 3

    owner_member = next((m for m in members if m.role == Role.OWNER), None)
    assert owner_member is not None

    from app.db.models import User

    you = (
        await db_session.execute(select(User).where(User.email == "you@researcherx.dev"))
    ).scalar_one()
    assert owner_member.user_id == you.id
