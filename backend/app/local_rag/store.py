"""The on-disk corpus: papers, chunks, and one vector matrix.

Layout under the store root:

    manifest.json    embedding model, dimension, chunk count, papers
    chunks.jsonl     one chunk per line, IN VECTOR ROW ORDER
    embeddings.npy   float32 (n_chunks, dim)
    bm25/            bm25s's own index directory (written by index.py)

ROW `i` OF `embeddings.npy`, LINE `i` OF `chunks.jsonl` AND DOCUMENT `i` OF
THE BM25 INDEX ARE THE SAME CHUNK. Nothing downstream can detect a break in
that alignment — a misaligned store returns a real score attached to the
wrong text, which reads as a plausible answer citing the wrong paper. So
`save` refuses a vector matrix whose row count disagrees with the chunk
list rather than writing it, and every reader indexes all three by the same
integer.

`np.save` rather than JSON for the vectors: 4.6k chunks at 768 float32 is
~14MB of binary against ~70MB of JSON text that also loses the dtype and
has to be re-parsed into an array on every query.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

_MANIFEST = "manifest.json"
_CHUNKS = "chunks.jsonl"
_VECTORS = "embeddings.npy"
BM25_DIRNAME = "bm25"


@dataclass(frozen=True)
class Paper:
    """One ingested document. `authors`/`year`/`venue` are for the prompt's
    metadata block and are absent, never guessed, when the source did not
    state them."""

    paper_id: str
    title: str
    authors: tuple[str, ...] = ()
    year: int | None = None
    venue: str | None = None
    source_path: str | None = None

    def to_json(self) -> dict:
        return {
            "paper_id": self.paper_id,
            "title": self.title,
            "authors": list(self.authors),
            "year": self.year,
            "venue": self.venue,
            "source_path": self.source_path,
        }

    @classmethod
    def from_json(cls, raw: dict) -> "Paper":
        return cls(
            paper_id=raw["paper_id"],
            title=raw["title"],
            authors=tuple(raw.get("authors") or ()),
            year=raw.get("year"),
            venue=raw.get("venue"),
            source_path=raw.get("source_path"),
        )


@dataclass(frozen=True)
class Chunk:
    paper_id: str
    paper_title: str
    chunk_index: int
    text: str

    @property
    def chunk_id(self) -> str:
        """Stable across a re-index, unlike the row number: re-chunking a
        paper renumbers rows but `paper:index` still names the same span of
        that paper's text."""
        return f"{self.paper_id}:{self.chunk_index}"

    def to_json(self) -> dict:
        return {
            "paper_id": self.paper_id,
            "paper_title": self.paper_title,
            "chunk_index": self.chunk_index,
            "text": self.text,
        }

    @classmethod
    def from_json(cls, raw: dict) -> "Chunk":
        return cls(
            paper_id=raw["paper_id"],
            paper_title=raw["paper_title"],
            chunk_index=raw["chunk_index"],
            text=raw["text"],
        )


@dataclass
class LocalStore:
    root: Path
    papers: list[Paper]
    chunks: list[Chunk]
    vectors: np.ndarray
    embedding_model: str
    paper_by_id: dict[str, Paper] = field(init=False)

    def __post_init__(self) -> None:
        self.paper_by_id = {p.paper_id: p for p in self.papers}

    @property
    def bm25_dir(self) -> Path:
        return self.root / BM25_DIRNAME

    @staticmethod
    def exists(root: Path) -> bool:
        return (Path(root) / _MANIFEST).is_file()

    @staticmethod
    def save(
        root: Path,
        *,
        papers: list[Paper],
        chunks: list[Chunk],
        vectors: np.ndarray,
        embedding_model: str,
    ) -> None:
        """Write the corpus, replacing whatever was there.

        Replace rather than append, for the reason `index_chunks` deletes
        before inserting: re-ingest is the documented repair path, and a
        repair that doubles the corpus is not one.
        """
        if len(chunks) != vectors.shape[0]:
            raise ValueError(
                f"refusing to save a misaligned store: {len(chunks)} chunks against "
                f"{vectors.shape[0]} vector rows. Row i and chunk i must be the same "
                "chunk; nothing downstream can detect otherwise."
            )
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        vectors = np.asarray(vectors, dtype=np.float32)

        (root / _CHUNKS).write_text(
            "".join(json.dumps(c.to_json(), ensure_ascii=False) + "\n" for c in chunks)
        )
        np.save(root / _VECTORS, vectors)
        (root / _MANIFEST).write_text(
            json.dumps(
                {
                    "embedding_model": embedding_model,
                    "dim": int(vectors.shape[1]) if vectors.ndim == 2 else 0,
                    "n_chunks": len(chunks),
                    "papers": [p.to_json() for p in papers],
                },
                indent=2,
                ensure_ascii=False,
            )
        )

    @classmethod
    def load(cls, root: Path) -> "LocalStore":
        root = Path(root)
        if not cls.exists(root):
            raise FileNotFoundError(
                f"no local RAG index at {root} — run `python -m app.local_rag.cli ingest` first"
            )
        manifest = json.loads((root / _MANIFEST).read_text())
        chunk_lines = (root / _CHUNKS).read_text().splitlines()
        chunks = [Chunk.from_json(json.loads(line)) for line in chunk_lines if line.strip()]
        vectors = np.load(root / _VECTORS)
        if len(chunks) != vectors.shape[0]:
            raise ValueError(
                f"corrupt store at {root}: {len(chunks)} chunks against "
                f"{vectors.shape[0]} vector rows. Re-ingest."
            )
        return cls(
            root=root,
            papers=[Paper.from_json(p) for p in manifest.get("papers", [])],
            chunks=chunks,
            vectors=vectors,
            embedding_model=manifest["embedding_model"],
        )
