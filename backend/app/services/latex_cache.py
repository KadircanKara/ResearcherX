"""In-memory cache of compiled artifacts, keyed by `tree_hash`
(`latex_files_service.tree_hash`) over the WHOLE document tree, not just the
main file.

The PDF is DERIVED: the source of truth is the `latex_files` tree in
Postgres (read through `latex_files_service`). A cache miss simply re-reads
every file in the tree and recompiles. Nothing here is persisted, and
nothing here may be treated as durable.

Valid only because uvicorn runs a single worker -- the same invariant the
event bus and the in-memory rate limiter already depend on. Under multiple
workers each process would hold a different PDF for the same document.

The SyncTeX map, the extraction root and the main file's tree-relative path
are stored BESIDE the PDF because a sync query needs all four: evicting them
separately would leave a PDF that cannot be navigated. `source` is not one of
them -- both sync directions answer from the PDF and the map alone (see
`latex_compiler.synctex_forward`/`synctex_reverse`), so keeping the source
text here would only be dead weight against `size`'s eviction accounting.
"""

from collections import OrderedDict
from dataclasses import dataclass

from app.core.config import settings


@dataclass(frozen=True)
class CachedBuild:
    pdf: bytes
    synctex_gz: bytes | None
    log: str
    # The extraction directory the tree was compiled in (None for a
    # degraded/no-tree build), and the tree-relative path of the main file --
    # both are what a sync query needs alongside the PDF and the map, now
    # that the wire protocol is tree-relative. `source` is gone: it is not
    # read here and was never durable (see the module docstring).
    root: str | None
    main_path: str

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
        compile fails.

        Promotes on read, exactly as `get` does. Without that, the fallback
        PDF gets no protection from the eviction pressure it is supposed to
        survive: a user whose compiles keep failing keeps asking for this
        build, and unrelated documents' traffic evicts it anyway. Measured in
        review -- five consecutive reads did not save it.
        """
        key = self._latest.get(document_id)
        if key is None:
            return None
        build = self._entries.get(key)
        if build is not None:
            self._entries.move_to_end(key)
        return build

    def _evict(self) -> None:
        while len(self._entries) > self._max_entries or self._total_bytes() > self._max_bytes:
            if len(self._entries) <= 1:
                # Never evict the only entry: an oversized single PDF would
                # otherwise be dropped the moment it lands, making every
                # request recompile it forever.
                return
            key, _ = self._entries.popitem(last=False)
            self._forget(key)

    def _forget(self, key: str) -> None:
        """Drop document pointers to an evicted key.

        `_latest` is otherwise never pruned and grows for the life of the
        process -- measured in review at 100,000 live mappings after 100,000
        one-off documents, while `_entries` stayed correctly bounded. A
        dangling pointer is also dead weight: `latest_for` would look up a key
        that is gone and return None anyway.
        """
        for document_id in [d for d, k in self._latest.items() if k == key]:
            del self._latest[document_id]

    def _total_bytes(self) -> int:
        return sum(build.size for build in self._entries.values())


cache = LatexCache()
