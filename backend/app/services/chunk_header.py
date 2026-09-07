"""The header that travels with a chunk.

PURE, no imports from app. Called at index time (`index_chunks`) to build
the string that is EMBEDDED, and at read time (`ChatAgent`) to build the
EXCERPT the model sees. One function each, so the two cannot drift.

Title and section go into the vector: they are what let a chunk be found by
the name of the section it sits in — the failure this fixes was a chunk
whose section heading landed in the PREVIOUS chunk, so the answer carried
none of the words that named it. Page stays out of the vector: a numeral
carries no meaning to an embedding model and matches only other numerals.
It is shown to the model as a locator.

The stored `text` and the citation snippet are never touched by this
module. The header is composed, not persisted.
"""

from __future__ import annotations

from collections.abc import Sequence

SEPARATOR = " > "


def section_path_text(section: Sequence[str]) -> str:
    return SEPARATOR.join(s for s in section if s)


def _header(title: str, section: Sequence[str], page: int | None) -> str:
    fields = [f"Title: {title}"]
    path = section_path_text(section)
    if path:
        fields.append(f"Section: {path}")
    if page is not None:
        fields.append(f"Page: {page}")
    return "[" + " | ".join(fields) + "]"


def embed_text(title: str, section: Sequence[str], text: str) -> str:
    """What gets embedded. Never includes the page."""
    return f"{_header(title, section, None)}\n\n{text}"


def excerpt_text(title: str, section: Sequence[str], page: int | None, text: str) -> str:
    """What the model reads in the excerpt catalog."""
    return f"{_header(title, section, page)}\n\n{text}"
