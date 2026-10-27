"""Build one project's bm25s index as an atomic, versioned directory.

Runs as a SUBPROCESS (`python -m app.services.bm25_build <project_id>
<version> <root>`), so a big tokenisation never holds the API's event loop or
its memory, and it opens its own engine instead of sharing the app's pool.

A version directory is complete only when it holds `DONE`. It is written to a
sibling `<version>.tmp.<pid>.<random>` directory, unique to this attempt, `DONE`
last, then renamed into place: a crash leaves either nothing or a `.tmp.*`
leftover (the scheduler's GC removes it), never a half-written version a loader
could pick up. Versions are unique -- an existing target is an error.

A project with nothing to index (no ready chunks, or text that is all
stopwords) gets a valid directory with an `EMPTY` marker and no bm25s files;
the loader treats that as "no lexical hits".
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import sys
import uuid
from collections.abc import Sequence
from pathlib import Path

import bm25s
import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings
from app.services.bm25_tokenizer import TOKENIZER_CONFIG, index_text, tokenize

log = logging.getLogger(__name__)

DONE = "DONE"
EMPTY = "EMPTY"

_CHUNKS_SQL = text("""
    SELECT c.id AS id, c.section_text AS section_text, c.text AS text
    FROM paper_chunk_embeddings c
    JOIN papers p ON p.id = c.paper_id
    WHERE c.model = :model
      AND c.project_id = :project_id
      AND p.status = 'ready'
    ORDER BY c.id
""")


class EmptyCorpus(Exception):
    """Nothing indexable: no documents, or every document tokenises to nothing."""


def build_index(chunk_ids: Sequence[str], texts: Sequence[str]) -> bm25s.BM25:
    if len(chunk_ids) != len(texts):
        raise ValueError("chunk_ids and texts must be the same length")
    if not texts:
        raise EmptyCorpus("no documents")
    tokens = tokenize(list(texts), return_ids=True)
    if not tokens.vocab:
        # bm25s raises an opaque ValueError on an empty vocabulary.
        raise EmptyCorpus("every document tokenises to nothing")
    retriever = bm25s.BM25()
    retriever.index(tokens, show_progress=False)
    return retriever


def _fsync_tree(directory: Path) -> None:
    for f in directory.iterdir():
        if f.is_file():
            fd = os.open(f, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)


def _write_atomically(out_dir: Path, retriever: bm25s.BM25 | None, chunk_ids: Sequence[str]):
    out_dir = Path(out_dir)
    if out_dir.exists():
        raise FileExistsError(f"index version already exists: {out_dir}")
    # Unique per attempt: an orphaned build of the same version (its parent
    # died, so its lock is gone) can never write into this directory, and the
    # rename onto a non-empty target fails, so two builds never mix.
    tmp = out_dir.with_name(f"{out_dir.name}.tmp.{os.getpid()}.{uuid.uuid4().hex[:12]}")
    tmp.mkdir(parents=True)
    try:
        if retriever is not None:
            retriever.save(str(tmp))
        else:
            (tmp / EMPTY).write_text("0\n")
        np.save(tmp / "chunk_ids.npy", np.asarray(list(chunk_ids), dtype=str))
        (tmp / "tokenizer.json").write_text(json.dumps(TOKENIZER_CONFIG))
        _fsync_tree(tmp)
        (tmp / DONE).write_text("1\n")
        _fsync_tree(tmp)
        os.rename(tmp, out_dir)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def save_version(out_dir: Path, retriever: bm25s.BM25, chunk_ids: Sequence[str]) -> None:
    """Write `retriever` and its id map to `out_dir`, atomically."""
    _write_atomically(out_dir, retriever, chunk_ids)


def save_empty_version(out_dir: Path) -> None:
    _write_atomically(out_dir, None, [])


def build_version(out_dir: Path, chunk_ids: Sequence[str], texts: Sequence[str]) -> int:
    """Index and save; an empty corpus writes the empty marker. Returns the count."""
    try:
        retriever = build_index(chunk_ids, texts)
    except EmptyCorpus as exc:
        log.info("bm25 build: empty corpus (%s)", exc)
        save_empty_version(out_dir)
        return 0
    save_version(out_dir, retriever, chunk_ids)
    return len(chunk_ids)


async def build_project(project_id: str, version: int, root: Path) -> int:
    """Index a project's ready chunks into `root/<version>`; returns the count."""
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    _CHUNKS_SQL, {"model": settings.embedding_model, "project_id": project_id}
                )
            ).fetchall()
    finally:
        await engine.dispose()
    ids = [r.id for r in rows]
    texts = [index_text(r.section_text, r.text) for r in rows]
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    return await asyncio.to_thread(build_version, root / str(version), ids, texts)


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    parser = argparse.ArgumentParser(prog="python -m app.services.bm25_build")
    parser.add_argument("project_id")
    parser.add_argument("version", type=int)
    parser.add_argument("root", type=Path)
    args = parser.parse_args(argv)
    try:
        count = asyncio.run(build_project(args.project_id, args.version, args.root))
    except Exception:
        log.exception("bm25 build failed")
        return 1
    print(count)
    return 0


if __name__ == "__main__":
    sys.exit(main())
