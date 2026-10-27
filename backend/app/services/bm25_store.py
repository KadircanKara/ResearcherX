"""Serve per-project bm25s indexes from an mmap LRU cache.

`Bm25Store.get` resolves a project's current index version from
`bm25_index_state` (the read is cached for `version_ttl_s`), loads that version
directory off the event loop with `mmap=True`, and keeps the `max_projects`
most recently used. A project with no index yet, or whose version directory is
missing, incomplete, corrupt or stamped by another tokenizer version, yields
None: the sparse leg is simply empty for that turn, never an error (the
scheduler repairs a missing directory and rebuilds a stale tokenizer).

An `EMPTY` version loads as an index that returns no hits. `search` tokenises
the query with the shared tokenizer, and a scoped search masks the corpus with
bm25s's `weight_mask` so only the allowed chunks are ranked.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

import bm25s
import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import log
from app.db.models import Bm25IndexState
from app.services.bm25_build import DONE, EMPTY
from app.services.bm25_tokenizer import TOKENIZER_CONFIG, terms


@dataclass(frozen=True)
class LoadedIndex:
    project_id: str
    version: int
    retriever: bm25s.BM25 | None  # None = EMPTY version
    chunk_ids: np.ndarray
    position: dict[str, int]


def _load(project_id: str, version: int) -> LoadedIndex | None:
    directory = Path(settings.bm25_index_dir) / project_id / str(version)
    if not (directory / DONE).is_file():
        log.warning("bm25_index_dir_missing", project_id=project_id, version=version)
        return None
    try:
        # Every version the builder writes, EMPTY included, carries the
        # tokenizer config. One stamped by another version would tokenise
        # queries against a foreign vocabulary: load nothing (dense-only)
        # until the scheduler's rebuild publishes a matching version.
        stamped = json.loads((directory / "tokenizer.json").read_text()).get("version")
        if stamped != TOKENIZER_CONFIG["version"]:
            log.warning(
                "bm25_index_tokenizer_mismatch",
                project_id=project_id,
                version=version,
                index_tokenizer=stamped,
                tokenizer=TOKENIZER_CONFIG["version"],
            )
            return None
        chunk_ids = np.load(directory / "chunk_ids.npy")
        retriever = (
            None if (directory / EMPTY).exists() else bm25s.BM25.load(str(directory), mmap=True)
        )
    except Exception as exc:  # corrupt or truncated index: lexical leg is empty, never an error
        log.warning(
            "bm25_index_load_failed", project_id=project_id, version=version, error=str(exc)
        )
        return None
    position = {str(cid): i for i, cid in enumerate(chunk_ids)}
    return LoadedIndex(project_id, version, retriever, chunk_ids, position)


class Bm25Store:
    def __init__(self, max_projects: int = 8, version_ttl_s: float = 5.0) -> None:
        self._max = max_projects
        self._ttl = version_ttl_s
        self._cache: OrderedDict[str, LoadedIndex] = OrderedDict()
        self._versions: dict[str, tuple[float, int]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def evict(self, project_id: str) -> None:
        """Forget a project (its directory was removed): drop the mmap and
        the cached version read."""
        self._cache.pop(project_id, None)
        self._versions.pop(project_id, None)
        self._locks.pop(project_id, None)

    async def _current_version(self, db: AsyncSession, project_id: str) -> int:
        now = time.monotonic()
        hit = self._versions.get(project_id)
        if hit is not None and now - hit[0] < self._ttl:
            return hit[1]
        version = (
            await db.execute(
                select(Bm25IndexState.current_version).where(
                    Bm25IndexState.project_id == project_id
                )
            )
        ).scalar_one_or_none()
        version = int(version or 0)
        self._versions[project_id] = (time.monotonic(), version)
        return version

    async def get(self, db: AsyncSession, project_id: str) -> LoadedIndex | None:
        version = await self._current_version(db, project_id)
        if version == 0:
            return None
        cached = self._cache.get(project_id)
        if cached is not None and cached.version == version:
            self._cache.move_to_end(project_id)
            return cached
        lock = self._locks.setdefault(project_id, asyncio.Lock())
        async with lock:
            cached = self._cache.get(project_id)
            if cached is not None and cached.version == version:
                self._cache.move_to_end(project_id)
                return cached
            loaded = await asyncio.to_thread(_load, project_id, version)
            if loaded is None:
                self._cache.pop(project_id, None)
                return None
            self._cache[project_id] = loaded
            self._cache.move_to_end(project_id)
            while len(self._cache) > self._max:
                evicted, _ = self._cache.popitem(last=False)
                self._locks.pop(evicted, None)
            return loaded

    def search(
        self,
        idx: LoadedIndex,
        query: str,
        limit: int,
        allowed_ids: Collection[str] | None = None,
    ) -> list[str]:
        """Chunk ids best-first; zero scores dropped. `allowed_ids` scopes the ranking."""
        if idx.retriever is None or limit <= 0:
            return []
        query_terms = terms(query)
        if not query_terms:
            return []
        size = len(idx.chunk_ids)
        mask = None
        if allowed_ids is not None:
            mask = np.zeros(size, dtype=np.float32)
            for cid in allowed_ids:
                pos = idx.position.get(cid)
                if pos is not None:
                    mask[pos] = 1.0
            candidates = int(mask.sum())
            if candidates == 0:
                return []
        else:
            candidates = size
        k = min(limit, candidates)
        kwargs = {"weight_mask": mask} if mask is not None else {}
        docs, scores = idx.retriever.retrieve(
            [query_terms], k=k, show_progress=False, n_threads=1, **kwargs
        )
        return [str(idx.chunk_ids[int(d)]) for d, s in zip(docs[0], scores[0]) if s > 0]


store = Bm25Store()
