"""app/local_rag/store.py — the on-disk corpus.

There is no database here. Row `i` of `embeddings.npy`, line `i` of
`chunks.jsonl` and document `i` of the BM25 index are the SAME chunk, and
that alignment is the store's whole contract: nothing else in the system
can detect a misalignment, it just returns the wrong text for the right
score. Every test below exists to pin one edge of it.
"""

import json
from pathlib import Path

import numpy as np
import pytest

from app.local_rag.store import Chunk, LocalStore, Paper

DIM = 8


def _paper(paper_id: str = "p1", title: str = "A Paper About Swarms") -> Paper:
    return Paper(paper_id=paper_id, title=title, authors=("Güven, E.",), year=2024)


def _chunks(paper: Paper, n: int) -> list[Chunk]:
    return [
        Chunk(
            paper_id=paper.paper_id,
            paper_title=paper.title,
            chunk_index=i,
            text=f"chunk number {i}",
        )
        for i in range(n)
    ]


def _vectors(n: int) -> np.ndarray:
    return np.arange(n * DIM, dtype=np.float32).reshape(n, DIM)


def test_save_then_load_round_trips_papers_chunks_and_vectors(tmp_path: Path):
    paper = _paper()
    chunks = _chunks(paper, 3)
    vectors = _vectors(3)

    LocalStore.save(tmp_path, papers=[paper], chunks=chunks, vectors=vectors, embedding_model="m")
    store = LocalStore.load(tmp_path)

    assert store.papers == [paper]
    assert store.chunks == chunks
    assert store.embedding_model == "m"
    np.testing.assert_array_equal(store.vectors, vectors)


def test_vectors_are_stored_as_a_single_npy_float32_matrix(tmp_path: Path):
    """np.save, not JSON: 4.6k chunks x 768 float32 is 14MB binary and ~70MB
    of JSON text that also loses the dtype."""
    paper = _paper()
    LocalStore.save(
        tmp_path,
        papers=[paper],
        chunks=_chunks(paper, 2),
        vectors=np.zeros((2, DIM), dtype=np.float64),
        embedding_model="m",
    )

    raw = np.load(tmp_path / "embeddings.npy")
    assert raw.dtype == np.float32
    assert raw.shape == (2, DIM)


def test_save_refuses_a_vector_matrix_that_does_not_line_up_with_the_chunks(tmp_path: Path):
    """The one corruption nothing downstream can see: scores from row i
    rendered as the text of chunk j."""
    paper = _paper()
    with pytest.raises(ValueError, match="3 chunks.*2 vector rows"):
        LocalStore.save(
            tmp_path,
            papers=[paper],
            chunks=_chunks(paper, 3),
            vectors=_vectors(2),
            embedding_model="m",
        )


def test_chunk_order_on_disk_is_the_vector_row_order(tmp_path: Path):
    paper = _paper()
    chunks = _chunks(paper, 4)
    LocalStore.save(
        tmp_path, papers=[paper], chunks=chunks, vectors=_vectors(4), embedding_model="m"
    )

    lines = (tmp_path / "chunks.jsonl").read_text().strip().split("\n")
    assert [json.loads(line)["chunk_index"] for line in lines] == [0, 1, 2, 3]


def test_load_reports_the_missing_directory_rather_than_an_empty_corpus(tmp_path: Path):
    """An empty store and an absent one answer every query identically —
    with nothing — so the absent one has to say so."""
    with pytest.raises(FileNotFoundError, match="no local RAG index"):
        LocalStore.load(tmp_path / "never-built")


def test_exists_is_false_before_a_save_and_true_after(tmp_path: Path):
    assert LocalStore.exists(tmp_path) is False
    paper = _paper()
    LocalStore.save(
        tmp_path, papers=[paper], chunks=_chunks(paper, 1), vectors=_vectors(1), embedding_model="m"
    )
    assert LocalStore.exists(tmp_path) is True


def test_an_empty_corpus_saves_and_loads_as_empty(tmp_path: Path):
    """Ingesting a PDF whose text extraction produced nothing must leave a
    readable store, not a half-written one."""
    LocalStore.save(
        tmp_path,
        papers=[],
        chunks=[],
        vectors=np.zeros((0, DIM), dtype=np.float32),
        embedding_model="m",
    )
    store = LocalStore.load(tmp_path)
    assert store.chunks == []
    assert store.vectors.shape == (0, DIM)


def test_chunk_id_is_paper_id_and_index(tmp_path: Path):
    """Stable across re-indexing, unlike a row number."""
    chunk = _chunks(_paper(), 3)[2]
    assert chunk.chunk_id == "p1:2"


def test_paper_titles_resolve_by_id(tmp_path: Path):
    paper = _paper()
    LocalStore.save(
        tmp_path, papers=[paper], chunks=_chunks(paper, 1), vectors=_vectors(1), embedding_model="m"
    )
    store = LocalStore.load(tmp_path)
    assert store.paper_by_id["p1"].title == "A Paper About Swarms"


def test_saving_twice_replaces_rather_than_appends(tmp_path: Path):
    """Re-ingest is the documented repair path; it must be idempotent the
    way index_chunks is."""
    paper = _paper()
    LocalStore.save(
        tmp_path, papers=[paper], chunks=_chunks(paper, 3), vectors=_vectors(3), embedding_model="m"
    )
    LocalStore.save(
        tmp_path, papers=[paper], chunks=_chunks(paper, 1), vectors=_vectors(1), embedding_model="m"
    )

    store = LocalStore.load(tmp_path)
    assert len(store.chunks) == 1
    assert store.vectors.shape == (1, DIM)
