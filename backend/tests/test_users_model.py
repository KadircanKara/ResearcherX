from sqlalchemy import select
from app.db.models import User


async def test_user_persists_with_defaults(db_session):
    db_session.add(User(email="a@x.io", name="A"))
    await db_session.commit()
    got = (await db_session.execute(select(User).where(User.email == "a@x.io"))).scalar_one()
    assert got.name == "A"
    assert got.avatar_color == "#2D3FE0"  # default
    assert got.password_hash is None  # auth deferred
    assert len(got.id) == 36
