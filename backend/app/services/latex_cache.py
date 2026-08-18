"""In-memory cache of compiled artifacts, keyed by source hash.

The PDF is DERIVED: the source of truth is `latex_documents.source` in
Postgres, and a cache miss simply recompiles. Nothing here is persisted, and
nothing here may be treated as durable.

Valid only because uvicorn runs a single worker -- the same invariant the
event bus and the in-memory rate limiter already depend on. Under multiple
workers each process would hold a different PDF for the same document.

The source and the SyncTeX map are stored BESIDE the PDF because a sync query
needs all three: evicting them separately would leave a PDF that cannot be
navigated.
"""

import hashlib
from collections import OrderedDict
from dataclasses import dataclass

from app.core.config import settings


def source_hash(source: str, engine: str) -> str:
    """Cache key. The engine is part of it: the same source compiled by
    xelatex is a different document, and keying on source alone would serve
    the wrong artifact after an engine switch."""
    digest = hashlib.sha256()
    digest.update(engine.encode("utf-8"))
    digest.update(b"\0")
    digest.update(source.encode("utf-8"))
    return digest.hexdigest()


@dataclass(frozen=True)
class CachedBuild:
    source: str
    pdf: bytes
    synctex_gz: bytes | None
    log: str

    @property
    def size(self) -> int:
        return len(self.pdf) + len(self.synctex_gz or b"")


class LatexCache:
    def __init__(self, max_entries: int | None = None, max_bytes: int | None = None) -> None:
        self._entries: OrderedDict[str, CachedBuild] = OrderedDict()
        self._latest: dict[str, str] = {}
        self._max_entries = max_entries if max_entries is not None else settings.latex_cache_entries
        self._max_bytes = max_bytes if max_bytes is not None else settings.latex_cache_bytes

    def put(self, key: str, build: CachedBuild, document_id: str | None = None) -> None:
        self._entries[key] = build
        self._entries.move_to_end(key)
        if document_id:
            self._latest[document_id] = key
        self._evict()

    def get(self, key: str) -> CachedBuild | None:
        build = self._entries.get(key)
        if build is None:
            return None
        # Reading makes it newest: the document being actively edited is the
        # one whose artifacts must survive eviction.
        self._entries.move_to_end(key)
        return build

    def latest_for(self, document_id: str) -> CachedBuild | None:
        """The last successful build of a document, whatever its source is
        now. This is what keeps the last good PDF on screen when the next
        compile fails."""
        key = self._latest.get(document_id)
        return self._entries.get(key) if key else None

    def _evict(self) -> None:
        while len(self._entries) > self._max_entries or self._total_bytes() > self._max_bytes:
            if len(self._entries) <= 1:
                # Never evict the only entry: an oversized single PDF would
                # otherwise be dropped the moment it lands, making every
                # request recompile it forever.
                return
            self._entries.popitem(last=False)

    def _total_bytes(self) -> int:
        return sum(build.size for build in self._entries.values())


cache = LatexCache()
