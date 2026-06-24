from sqlalchemy import func, select
from app.db.models import User
from app.db.seed import seed_users


async def test_seed_is_idempotent(db_session):
    await seed_users(db_session)
    await db_session.commit()
    await seed_users(db_session)
    await db_session.commit()
    count = (await db_session.execute(select(func.count()).select_from(User))).scalar_one()
    assert count == 3
