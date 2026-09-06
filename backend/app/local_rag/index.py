"""The BM25 arm, on `bm25s` (numpy/scipy, no service to run).

This is the real BM25 the Postgres path never had: `ts_rank_cd` carries no
IDF and no length normalisation, which is why the SQL sparse arm needed a
lowered RRF `k` before it could contribute at all (CLAUDE.md).

Index and query MUST tokenise identically — same stopword list, same
stemmer — or a query term never matches its own indexed form. `_tokenize`
is the single place that decides it, and both paths call it.

Queries are tokenised with `return_ids=False`, i.e. as plain strings, so
the query is looked up in the INDEX's vocabulary. A `Tokenized` object
built at query time carries a vocabulary of its own, and matching ids
across two vocabularies is meaningless.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import bm25s
import Stemmer

_STOPWORDS = "english"


def _stemmer() -> Stemmer.Stemmer:
    return Stemmer.Stemmer("english")


def _tokenize(texts: list[str], *, return_ids: bool):
    return bm25s.tokenize(
        texts,
        stopwords=_STOPWORDS,
        stemmer=_stemmer(),
        return_ids=return_ids,
        show_progress=False,
    )


@dataclass
class BM25Index:
    """Thin wrapper over `bm25s.BM25`, holding the two rules the raw library
    leaves to the caller: clamp `k` to the corpus, and drop zero scores."""

    retriever: bm25s.BM25 | None
    n_docs: int

    def search(self, query: str, limit: int) -> list[tuple[int, float]]:
        """`(document index, score)`, best first, zero scores removed.

        The zero-score filter is what makes this arm behave like the
        `tsv @@ tsquery` admission it replaces: bm25s returns the top k
        documents whatever their score, so an unmatched query would
        otherwise hand RRF a full slate of arbitrary documents to rank.
        """
        if self.retriever is None or self.n_docs == 0 or limit <= 0:
            return []
        # bm25s RAISES when k > corpus size, where a SQL LIMIT returns fewer.
        k = min(limit, self.n_docs)
        tokens = _tokenize([query], return_ids=False)
        indices, scores = self.retriever.retrieve(tokens, k=k, show_progress=False)
        return [
            (int(doc), float(score))
            for doc, score in zip(indices[0], scores[0], strict=True)
            if score > 0
        ]


def build_bm25(texts: list[str]) -> BM25Index:
    if not texts:
        return BM25Index(retriever=None, n_docs=0)
    retriever = bm25s.BM25()
    retriever.index(_tokenize(texts, return_ids=True), show_progress=False)
    return BM25Index(retriever=retriever, n_docs=len(texts))


def save_bm25(index: BM25Index, directory: Path) -> None:
    """Replace, never merge — the store's `save` has the same contract."""
    directory = Path(directory)
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if index.retriever is None:
        # An empty corpus has no bm25s index to write; the marker is what
        # `load_bm25` reads back so an empty store still round-trips.
        (directory / "empty.marker").write_text("0\n")
        return
    index.retriever.save(str(directory))


def load_bm25(directory: Path) -> BM25Index:
    directory = Path(directory)
    if not directory.is_dir():
        raise FileNotFoundError(
            f"no BM25 index at {directory} — run `python -m app.local_rag.cli ingest` first"
        )
    if (directory / "empty.marker").is_file():
        return BM25Index(retriever=None, n_docs=0)
    retriever = bm25s.BM25.load(str(directory), load_corpus=False)
    return BM25Index(retriever=retriever, n_docs=int(retriever.scores["num_docs"]))
