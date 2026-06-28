"""Request-scoped debug log collector (dev only).

Usage:
    debug_log.step("fn_name", key=value, ...)   # append a step entry
    debug_log.flush()                           # return entries and clear
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

_entries: ContextVar[list[dict[str, Any]] | None] = ContextVar("_debug_entries", default=None)


def start() -> None:
    _entries.set([])


def step(fn: str, **kwargs: Any) -> None:
    bucket = _entries.get()
    if bucket is not None:
        bucket.append({"fn": fn, **kwargs})


def flush() -> list[dict[str, Any]]:
    bucket = _entries.get()
    return bucket if bucket is not None else []
