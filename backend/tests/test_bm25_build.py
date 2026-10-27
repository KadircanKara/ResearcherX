"""bm25s build step: atomic versioned output. Real bm25s, sqlite for the DB read."""

import json
import os
from pathlib import Path

import bm25s
import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Paper, PaperChunkEmbedding, PaperStatus, Project, ProjectMember, User
from app.db.seed import seed_users
from app.services import bm25_build
from app.services.bm25_build import build_index, build_version, save_version
from app.services.bm25_tokenizer import TOKENIZER_CONFIG, index_text, tokenize

IDS = ["c1", "c2", "c3"]
TEXTS = [
    "particle swarm optimisation for target search",
    "battery constraints limit flight time",
    "task offloading to edge servers",
]


def _search(retriever: bm25s.BM25, query: str) -> list[int]:
    idx, _ = retriever.retrieve(tokenize([query], return_ids=False), k=1, show_progress=False)
    return [int(i) for i in idx[0]]


def test_saved_version_round_trips_ids(tmp_path: Path):
    out = tmp_path / "1"
    save_version(out, build_index(IDS, TEXTS), IDS)
    loaded = bm25s.BM25.load(str(out), load_corpus=False, mmap=True)
    ids = np.load(out / "chunk_ids.npy")
    assert ids[_search(loaded, "battery flight")[0]] == "c2"
    assert ids[_search(loaded, "edge offloading")[0]] == "c3"
    assert (out / "DONE").is_file()


def test_tokenizer_config_is_saved(tmp_path: Path):
    out = tmp_path / "1"
    save_version(out, build_index(IDS, TEXTS), IDS)
    assert json.loads((out / "tokenizer.json").read_text()) == TOKENIZER_CONFIG


def test_save_version_is_atomic(tmp_path: Path, monkeypatch):
    out = tmp_path / "1"

    def boom(*a, **k):
        raise OSError("crash before rename")

    monkeypatch.setattr(os, "rename", boom)
    with pytest.raises(OSError):
        save_version(out, build_index(IDS, TEXTS), IDS)
    assert not out.exists()


def test_a_stale_tmp_dir_is_replaced(tmp_path: Path):
    stale = tmp_path / "1.tmp"
    stale.mkdir()
    (stale / "junk").write_text("x")
    save_version(tmp_path / "1", build_index(IDS, TEXTS), IDS)
    assert not stale.exists()
    assert (tmp_path / "1" / "DONE").is_file()
    assert not (tmp_path / "1" / "junk").exists()


def test_an_existing_version_is_never_overwritten(tmp_path: Path):
    out = tmp_path / "1"
    save_version(out, build_index(IDS, TEXTS), IDS)
    with pytest.raises(FileExistsError):
        save_version(out, build_index(IDS, TEXTS), IDS)


@pytest.mark.parametrize("ids,texts", [([], []), (["a"], ["the and of"])])
def test_an_empty_or_all_stopword_corpus_writes_a_valid_empty_version(tmp_path, ids, texts):
    out = tmp_path / "1"
    assert build_version(out, ids, texts) == 0
    names = {p.name for p in out.iterdir()}
    assert names == {"chunk_ids.npy", "tokenizer.json", "DONE", "EMPTY"}
    assert len(np.load(out / "chunk_ids.npy")) == 0


def test_index_text_is_the_tsv_expression():
    assert index_text("4 Results", "battery") == "4 Results battery"
    assert index_text(None, "battery") == " battery"


def test_harness_arm_uses_the_production_build(monkeypatch):
    from evals.retrieval import bm25_arm

    calls = []
    real = bm25_arm.build_index

    def spy(ids, texts):
        calls.append(list(ids))
        return real(ids, texts)

    monkeypatch.setattr(bm25_arm, "build_index", spy)
    bm25_arm.Bm25Corpus([bm25_arm.CorpusChunk("c1", "p1", "T", "swarm search", "2 Method")])
    assert calls == [["c1"]]


@pytest.fixture
async def seeded(db_session: AsyncSession) -> str:
    await seed_users(db_session)
    you = (
        await db_session.execute(select(User).where(User.email == "you@researcherx.dev"))
    ).scalar_one()
    proj = Project(owner_id=you.id, title="B", topic_keywords=[])
    other = Project(owner_id=you.id, title="O", topic_keywords=[])
    db_session.add_all([proj, other])
    await db_session.flush()
    db_session.add(ProjectMember(project_id=proj.id, user_id=you.id, role="owner"))
    ready = Paper(project_id=proj.id, title="R", status=PaperStatus.READY)
    pending = Paper(project_id=proj.id, title="P", status=PaperStatus.PENDING)
    foreign = Paper(project_id=other.id, title="F", status=PaperStatus.READY)
    db_session.add_all([ready, pending, foreign])
    await db_session.flush()

    def chunk(paper, pid, cid, model, txt, i):
        return PaperChunkEmbedding(
            id=cid,
            paper_id=paper.id,
            project_id=pid,
            chunk_index=i,
            text=txt,
            section_text="2 Method",
            embedding="[]",
            model=model,
        )

    m = settings.embedding_model
    db_session.add_all(
        [
            chunk(ready, proj.id, "b", m, "battery flight", 0),
            chunk(ready, proj.id, "a", m, "swarm search", 1),
            chunk(ready, proj.id, "stale", "old-model", "swarm", 2),
            chunk(pending, proj.id, "pend", m, "swarm", 0),
            chunk(foreign, other.id, "frn", m, "swarm", 0),
        ]
    )
    await db_session.commit()
    return proj.id


async def test_build_project_reads_ready_chunks_of_this_project_and_model(
    seeded: str, tmp_path: Path
):
    n = await bm25_build.build_project(seeded, 7, tmp_path)
    assert n == 2
    assert list(np.load(tmp_path / "7" / "chunk_ids.npy")) == ["a", "b"]  # ordered by id


async def test_build_project_with_no_chunks_writes_an_empty_version(seeded: str, tmp_path: Path):
    assert await bm25_build.build_project("no-such-project", 1, tmp_path) == 0
    assert (tmp_path / "1" / "EMPTY").is_file()


def test_cli_prints_only_the_count_and_exits_1_on_error(tmp_path, capsys, monkeypatch):
    async def fake(pid, version, root):
        return 5

    monkeypatch.setattr(bm25_build, "build_project", fake)
    assert bm25_build.main(["p", "1", str(tmp_path)]) == 0
    assert capsys.readouterr().out == "5\n"

    async def bad(pid, version, root):
        raise RuntimeError("x")

    monkeypatch.setattr(bm25_build, "build_project", bad)
    assert bm25_build.main(["p", "1", str(tmp_path)]) == 1
    assert capsys.readouterr().out == ""
