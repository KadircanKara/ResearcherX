"""The `(n)` suffix rule for a name that is already taken.

PURE -- no DB, no ORM, no service imports -- like `latex_paths.py`,
`mention_ranker.py` and `text_matching.py`. Every decision here is made from
strings, so every decision here is testable without a database.

Modelled on a browser download: `intro.tex` next to an existing `intro.tex`
becomes `intro (1).tex`, and `(2)` after that. This is the ONLY
implementation of that rule in the system. The frontend displays the
`suggestion` this module produces and never recomputes it -- a second copy
would drift, and a drifting suffix means two clients disagreeing about which
file a `\\input` names.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from app.services.latex_paths import collision_key


@dataclass(frozen=True)
class Collision:
    """One incoming path that cannot be written as-is.

    `existing` is the taken path in ITS OWN spelling, not a folded key: under
    a case-only collision the user has to be shown the file they actually
    have.
    """

    path: str
    existing: str
    suggestion: str


def _split(name: str) -> tuple[str, str]:
    """`stem, extension` for the LAST dot only, with the extension including
    its dot (or empty).

    A leading dot is part of the name, never a separator: `.gitignore` has no
    extension to protect. Splitting on the last dot is Chrome's rule and is
    what makes `data.tar.gz` suffix as `data.tar (1).gz`.
    """
    dot = name.rfind(".")
    if dot <= 0:
        return name, ""
    return name[:dot], name[dot:]


def _free(candidate: str, taken_keys: Collection[str]) -> bool:
    return collision_key(candidate) not in taken_keys


def suffix_path(path: str, taken: Collection[str]) -> str:
    """`path` itself if free, else the first free `… (n)` spelling.

    Only the final segment is touched -- two trees sharing a `figures/`
    directory is not a collision, and suffixing the directory would scatter a
    merged project across `figures/` and `figures (1)/`.
    """
    taken_keys = {collision_key(t) for t in taken}
    if _free(path, taken_keys):
        return path

    head, _, leaf = path.rpartition("/")
    prefix = f"{head}/" if head else ""
    stem, ext = _split(leaf)
    n = 1
    while True:
        candidate = f"{prefix}{stem} ({n}){ext}"
        if _free(candidate, taken_keys):
            return candidate
        n += 1


def suffix_name(name: str, taken: Collection[str]) -> str:
    """The same rule for a DOCUMENT name, which has no extension to protect.

    A separate entry point rather than a flag on `suffix_path`: `My Paper
    v1.2` must not lose its `.2` to extension handling, and a caller passing
    a document name is never talking about a file.
    """
    taken_keys = {collision_key(t) for t in taken}
    if _free(name, taken_keys):
        return name
    n = 1
    while True:
        candidate = f"{name} ({n})"
        if _free(candidate, taken_keys):
            return candidate
        n += 1


def plan_writes(incoming: Sequence[str], taken: Collection[str]) -> list[Collision]:
    """Every `incoming` path that collides, with the name it would get.

    Numbered against ONE growing set, so two incoming `plot.png` become
    `(1)` and `(2)`. Numbering each against the original tree would hand both
    the same suggestion and lose one of them.

    `incoming` order is preserved and is the order the UI lists them in.
    """
    taken_keys = {collision_key(t): t for t in taken}
    out: list[Collision] = []
    for path in incoming:
        key = collision_key(path)
        existing = taken_keys.get(key)
        if existing is None:
            taken_keys[key] = path
            continue
        suggestion = suffix_path(path, taken_keys.values())
        taken_keys[collision_key(suggestion)] = suggestion
        out.append(Collision(path=path, existing=existing, suggestion=suggestion))
    return out


# Matches a name this module produced, for callers that want to display a
# suffixed name differently. Not used to UNWRAP a name -- see the test
# `test_an_already_suffixed_name_is_not_unwrapped` for why.
SUFFIXED = re.compile(r" \((\d+)\)(\.[^.]*)?$")
