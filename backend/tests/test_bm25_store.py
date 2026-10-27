"""Bm25Store: version-keyed mmap LRU cache and scoped search."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Bm25IndexState, Project, User
from app.db.seed import seed_users
from app.services import bm25_store
from app.services.bm25_build import build_version
from app.services.bm25_store import Bm25Store


@pytest.fixture(autouse=True)
def index_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "bm25_index_dir", str(tmp_path))
    return tmp_path


async def _project(db: AsyncSession, title: str, version: int = 0) -> str:
    await seed_users(db)
    you = (await db.execute(select(User).where(User.email == "you@researcherx.dev"))).scalar_one()
    p = Project(owner_id=you.id, title=title, topic_keywords=[])
    db.add(p)
    await db.flush()
    db.add(Bm25IndexState(project_id=p.id, current_version=version))
    await db.commit()
    return p.id


async def _set_version(db: AsyncSession, pid: str, version: int) -> None:
    row = await db.get(Bm25IndexState, pid)
    row.current_version = version
    await db.commit()


def _build(root, pid: str, version: int, docs: dict[str, str]) -> None:
    build_version(root / pid / str(version), list(docs), list(docs.values()))


DOCS = {
    "c1": "swarm search coordination of drones",
    "c2": "federated learning with communication",
    "c3": "drone swarm path planning",
}


async def test_get_returns_none_before_first_build(db_session):
    pid = await _project(db_session, "none")
    assert await Bm25Store().get(db_session, pid) is None


async def test_get_returns_none_when_version_dir_missing(db_session):
    pid = await _project(db_session, "missing", version=3)
    assert await Bm25Store().get(db_session, pid) is None


async def test_get_reloads_when_version_changes(db_session, index_dir):
    pid = await _project(db_session, "reload", version=1)
    _build(index_dir, pid, 1, {"c1": DOCS["c1"]})
    _build(index_dir, pid, 2, DOCS)
    s = Bm25Store(version_ttl_s=0)
    first = await s.get(db_session, pid)
    assert first.version == 1 and len(first.chunk_ids) == 1
    await _set_version(db_session, pid, 2)
    second = await s.get(db_session, pid)
    assert second.version == 2 and len(second.chunk_ids) == 3
    assert await s.get(db_session, pid) is second


async def test_version_check_is_cached_for_ttl(db_session, index_dir):
    pid = await _project(db_session, "ttl", version=1)
    _build(index_dir, pid, 1, {"c1": DOCS["c1"]})
    _build(index_dir, pid, 2, DOCS)
    s = Bm25Store(version_ttl_s=3600)
    assert (await s.get(db_session, pid)).version == 1
    await _set_version(db_session, pid, 2)
    assert (await s.get(db_session, pid)).version == 1


async def test_lru_evicts_least_recent_project(db_session, index_dir):
    pids = []
    for n in range(3):
        pid = await _project(db_session, f"p{n}", version=1)
        _build(index_dir, pid, 1, DOCS)
        pids.append(pid)
    s = Bm25Store(max_projects=2)
    a = await s.get(db_session, pids[0])
    await s.get(db_session, pids[1])
    assert await s.get(db_session, pids[0]) is a  # touch: p0 is now most recent
    await s.get(db_session, pids[2])  # evicts p1
    assert set(s._cache) == {pids[0], pids[2]}


async def test_search_scoped_by_weight_mask_returns_only_allowed(db_session, index_dir):
    pid = await _project(db_session, "scoped", version=1)
    _build(index_dir, pid, 1, DOCS)
    s = Bm25Store()
    idx = await s.get(db_session, pid)
    assert s.search(idx, "drone swarm", 10)[0] in {"c1", "c3"}
    assert s.search(idx, "drone swarm", 10, allowed_ids={"c3"}) == ["c3"]
    assert set(s.search(idx, "drone swarm", 10, allowed_ids={"c1", "c2"})) == {"c1"}


async def test_search_drops_zero_scores_and_unknown_ids(db_session, index_dir):
    pid = await _project(db_session, "zero", version=1)
    _build(index_dir, pid, 1, DOCS)
    s = Bm25Store()
    idx = await s.get(db_session, pid)
    assert s.search(idx, "federated", 10) == ["c2"]
    assert s.search(idx, "zzzunseen", 10) == []
    assert s.search(idx, "the of", 10) == []
    assert s.search(idx, "drone", 10, allowed_ids={"nope"}) == []
    assert s.search(idx, "drone", 10, allowed_ids={"nope", "c1"}) == ["c1"]


async def test_empty_version_loads_and_returns_no_hits(db_session, index_dir):
    pid = await _project(db_session, "empty", version=1)
    build_version(index_dir / pid / "1", [], [])
    s = Bm25Store()
    idx = await s.get(db_session, pid)
    assert idx is not None and s.search(idx, "drone", 5) == []


async def test_concurrent_gets_load_once(db_session, index_dir, monkeypatch):
    import asyncio

    pid = await _project(db_session, "conc", version=1)
    _build(index_dir, pid, 1, DOCS)
    calls = []
    real = bm25_store._load
    monkeypatch.setattr(bm25_store, "_load", lambda *a: calls.append(a) or real(*a))
    s = Bm25Store()
    await s._current_version(db_session, pid)  # one session cannot run concurrent queries
    await asyncio.gather(*(s.get(db_session, pid) for _ in range(4)))
    assert len(calls) == 1


async def test_corrupt_index_returns_none_and_drops_cache(db_session, index_dir):
    pid = await _project(db_session, "corrupt", version=1)
    _build(index_dir, pid, 1, DOCS)
    s = Bm25Store(version_ttl_s=0)
    assert await s.get(db_session, pid) is not None
    bad = index_dir / pid / "2"
    bad.mkdir()
    (bad / "DONE").write_text("1\n")
    (bad / "chunk_ids.npy").write_bytes(b"garbage")
    (bad / "params.index.json").write_text("{not json")
    await _set_version(db_session, pid, 2)
    assert await s.get(db_session, pid) is None
    assert pid not in s._cache


async def test_search_clamps_limit_and_orders_best_first(db_session, index_dir):
    pid = await _project(db_session, "clamp", version=1)
    docs = {
        "a": "zebra zebra zebra",
        "b": "zebra zebra apple",
        "c": "zebra apple banana",
        "d": "apple banana cherry",
    }
    _build(index_dir, pid, 1, docs)
    s = Bm25Store()
    idx = await s.get(db_session, pid)
    assert s.search(idx, "zebra", 100) == ["a", "b", "c"]
    assert s.search(idx, "zebra", 2) == ["a", "b"]


async def test_an_index_from_another_tokenizer_version_is_not_loaded(db_session, index_dir):
    import json

    pid = await _project(db_session, "oldtok", version=1)
    _build(index_dir, pid, 1, DOCS)
    cfg = index_dir / pid / "1" / "tokenizer.json"
    stale = json.loads(cfg.read_text())
    stale["version"] = stale["version"] - 1
    cfg.write_text(json.dumps(stale))
    assert await Bm25Store().get(db_session, pid) is None


async def test_an_empty_index_from_another_tokenizer_version_is_not_loaded(db_session, index_dir):
    import json

    pid = await _project(db_session, "oldtok-empty", version=1)
    build_version(index_dir / pid / "1", [], [])
    cfg = index_dir / pid / "1" / "tokenizer.json"
    assert cfg.is_file()  # the build stamps EMPTY versions too
    cfg.write_text(json.dumps({**json.loads(cfg.read_text()), "version": -1}))
    assert await Bm25Store().get(db_session, pid) is None


async def test_an_index_with_no_tokenizer_stamp_is_not_loaded(db_session, index_dir):
    pid = await _project(db_session, "nostamp", version=1)
    _build(index_dir, pid, 1, DOCS)
    (index_dir / pid / "1" / "tokenizer.json").unlink()
    assert await Bm25Store().get(db_session, pid) is None


def test_evict_forgets_a_project():
    s = Bm25Store()
    s._cache["p"] = object()
    s._versions["p"] = (0.0, 3)
    s.evict("p")
    s.evict("never-seen")
    assert "p" not in s._cache and "p" not in s._versions
