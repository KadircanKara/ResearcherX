"""Where a validated archive waits between `import/plan` and `import/commit`.

The plan step must tell the user which files collide, which needs the
archive's entry list, which only the server may compute -- `read_archive` is
where every traversal, symlink, encryption and decompression-bomb guard
lives, and the browser is never a second validator. Parking the parsed
entries here is what lets a merge upload its archive exactly ONCE instead of
uploading it, being told it collides, and uploading it again -- which for a
merge is the common path, not the rare one.

NOTHING here is durable. A restart drops every pending import and the user
re-uploads; the alternative is a table of half-finished imports nobody
reaps.

Valid ONLY because uvicorn runs a single worker -- the same invariant the
event bus, the in-memory rate limiter and `latex_cache` already rest on.
Under multiple workers a token minted by one process would 404 in another.

Time is a PARAMETER, never read from a clock inside this module, so the TTL
is tested without sleeping.
"""

from __future__ import annotations

import secrets
from collections import OrderedDict
from dataclasses import dataclass, field

from app.core.config import settings
from app.services.latex_archive import ArchiveEntry


class StagingNotFound(Exception):
    """No such token, or it belongs to someone else / another project."""


class StagingExpired(Exception):
    """The token was real and has aged out. Distinct from NotFound because
    the user needs a different sentence: re-upload, rather than something
    went wrong."""


@dataclass(frozen=True)
class StagedImport:
    project_id: str
    user_id: str
    entries: list[ArchiveEntry] = field(default_factory=list)

    @property
    def size(self) -> int:
        return sum(len(e.data) for e in self.entries)


class LatexStaging:
    def __init__(
        self,
        max_entries: int | None = None,
        max_bytes: int | None = None,
        ttl_s: int | None = None,
    ) -> None:
        self._entries: OrderedDict[str, tuple[StagedImport, float]] = OrderedDict()
        self._max_entries = (
            max_entries if max_entries is not None else settings.latex_staging_entries
        )
        self._max_bytes = max_bytes if max_bytes is not None else settings.latex_staging_bytes
        self._ttl_s = ttl_s if ttl_s is not None else settings.latex_staging_ttl_s

    def put(self, staged: StagedImport, *, now: float) -> str:
        token = secrets.token_urlsafe(24)
        self._entries[token] = (staged, now)
        self._entries.move_to_end(token)
        self._evict()
        return token

    def take(self, token: str, project_id: str, user_id: str, *, now: float) -> StagedImport:
        """Single use: a redeemed token is removed, so a double-clicked
        commit cannot import the same archive twice."""
        found = self._entries.get(token)
        if found is None:
            raise StagingNotFound(token)
        staged, created = found
        if staged.project_id != project_id or staged.user_id != user_id:
            # Same answer as an unknown token, deliberately: confirming that
            # a token exists but belongs to someone else is an answer nobody
            # needs.
            raise StagingNotFound(token)
        del self._entries[token]
        if now - created > self._ttl_s:
            raise StagingExpired(token)
        return staged

    def _evict(self) -> None:
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
        while (
            sum(s.size for s, _ in self._entries.values()) > self._max_bytes
            and len(self._entries) > 1
        ):
            self._entries.popitem(last=False)


staging = LatexStaging()
