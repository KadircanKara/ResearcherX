"""app/local_rag/ingest.py — sources on disk to a searchable store.

The embedding call is injected, so these run with no network and no
provider: what is under test is chunking, ordering, and the three-way
alignment between chunks, vectors and the BM25 index.
"""

from pathlib import Path

import numpy as np
import pytest

from app.local_rag.index import load_bm25
from app.local_rag.ingest import ingest
from app.local_rag.store import LocalStore

DIM = 6


async def _fake_embed(texts: list[str]) -> list[list[float]]:
    """Deterministic and distinct per text, so alignment bugs surface as a
    wrong vector rather than as a coincidence."""
    return [[float(len(t) % 7)] * DIM for t in texts]


def _write(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(body)
    return path


async def test_ingests_a_text_file_into_a_loadable_store(tmp_path: Path):
    source = _write(tmp_path, "swarms.txt", "swarm coverage planning " * 200)
    root = tmp_path / "index"

    report = await ingest(sources=[source], root=root, embed=_fake_embed)

    store = LocalStore.load(root)
    assert report.n_papers == 1
    assert report.n_chunks == len(store.chunks) > 0
    assert store.papers[0].title == "swarms"
    assert store.vectors.shape == (len(store.chunks), DIM)


async def test_chunk_indices_are_sequential_per_paper(tmp_path: Path):
    source = _write(tmp_path, "a.txt", "alpha beta gamma " * 500)
    root = tmp_path / "index"

    await ingest(sources=[source], root=root, embed=_fake_embed)

    store = LocalStore.load(root)
    assert [c.chunk_index for c in store.chunks] == list(range(len(store.chunks)))
    assert {c.paper_id for c in store.chunks} == {"a"}


async def test_every_chunk_row_carries_the_vector_of_its_own_text(tmp_path: Path):
    """The alignment invariant, checked end to end rather than asserted."""
    _write(tmp_path, "one.txt", "x " * 500)
    _write(tmp_path, "two.txt", "a much shorter document")
    root = tmp_path / "index"

    await ingest(sources=[tmp_path / "one.txt", tmp_path / "two.txt"], root=root, embed=_fake_embed)

    store = LocalStore.load(root)
    expected = await _fake_embed([c.text for c in store.chunks])
    np.testing.assert_allclose(store.vectors, np.array(expected, dtype=np.float32))


async def test_the_bm25_index_is_written_and_searchable(tmp_path: Path):
    _write(tmp_path, "swarms.txt", "swarm coverage path planning for aerial vehicles")
    _write(tmp_path, "batteries.txt", "battery chemistry and charging cycles")
    root = tmp_path / "index"

    await ingest(
        sources=[tmp_path / "swarms.txt", tmp_path / "batteries.txt"],
        root=root,
        embed=_fake_embed,
    )

    store = LocalStore.load(root)
    hits = load_bm25(store.bm25_dir).search("battery charging", limit=5)
    assert hits
    assert store.chunks[hits[0][0]].paper_id == "batteries"


async def test_multiple_papers_keep_their_own_titles_and_ids(tmp_path: Path):
    _write(tmp_path, "first.txt", "content one " * 50)
    _write(tmp_path, "second.txt", "content two " * 50)
    root = tmp_path / "index"

    await ingest(
        sources=[tmp_path / "first.txt", tmp_path / "second.txt"], root=root, embed=_fake_embed
    )

    store = LocalStore.load(root)
    assert {p.paper_id for p in store.papers} == {"first", "second"}
    assert {c.paper_title for c in store.chunks} == {"first", "second"}


async def test_re_ingesting_replaces_rather_than_appends(tmp_path: Path):
    source = _write(tmp_path, "a.txt", "alpha " * 400)
    root = tmp_path / "index"
    await ingest(sources=[source], root=root, embed=_fake_embed)
    first = len(LocalStore.load(root).chunks)

    source.write_text("alpha")
    await ingest(sources=[source], root=root, embed=_fake_embed)

    store = LocalStore.load(root)
    assert len(store.chunks) == 1 < first


async def test_a_source_with_no_text_leaves_a_valid_empty_store(tmp_path: Path):
    source = _write(tmp_path, "blank.txt", "   \n  ")
    root = tmp_path / "index"

    report = await ingest(sources=[source], root=root, embed=_fake_embed)

    store = LocalStore.load(root)
    assert report.n_chunks == 0
    assert store.chunks == []
    assert load_bm25(store.bm25_dir).search("anything", limit=3) == []


async def test_an_unreadable_source_type_is_refused_by_name(tmp_path: Path):
    source = _write(tmp_path, "notes.docx", "whatever")
    with pytest.raises(ValueError, match="notes.docx"):
        await ingest(sources=[source], root=tmp_path / "index", embed=_fake_embed)


async def test_a_missing_source_is_refused_by_name(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="ghost.txt"):
        await ingest(sources=[tmp_path / "ghost.txt"], root=tmp_path / "index", embed=_fake_embed)


async def test_the_embedding_model_is_recorded_in_the_store(tmp_path: Path):
    """Reusing an index under a different embedding model silently compares
    two vector spaces; the recorded name is what makes that detectable."""
    source = _write(tmp_path, "a.txt", "alpha " * 100)
    root = tmp_path / "index"

    await ingest(sources=[source], root=root, embed=_fake_embed, embedding_model="my-model")

    assert LocalStore.load(root).embedding_model == "my-model"
