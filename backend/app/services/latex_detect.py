"""Choosing the main file and the engine for an imported project.

PURE -- no DB, no ORM, no service imports.

Detection NEVER picks between equals. `paper_resolver.py` holds the same line
for the same reason: an LLM targeter that guessed a paper measured worse than
no targeter at all, because a confident wrong answer is acted on while an
honest question is answered. A wrong `main_path` compiles the wrong document
and the user has no way to see why.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

# Only these need a Unicode engine, and each hard-FAILS under pdflatex -- so
# defaulting to pdflatex guarantees a broken first compile on exactly the
# projects most likely to arrive from Overleaf.
_XETEX_PACKAGES = ("fontspec", "unicode-math", "polyglossia")

_PREFERRED_ROOT_NAMES = ("main.tex", "paper.tex", "ms.tex")

# A line whose first non-space character is % is a comment. Checked per line
# rather than with a global regex so a commented-out preamble from an earlier
# draft cannot select the engine or nominate a main file.
_COMMENT = re.compile(r"^\s*%")


class AmbiguousMain(Exception):
    def __init__(self, paths: list[str]) -> None:
        super().__init__(f"several files could be the main file: {', '.join(sorted(paths))}")
        self.paths = sorted(paths)


class NoMainFile(Exception):
    def __init__(self) -> None:
        super().__init__("no LaTeX main file found in the archive")


def _uncommented(source: str) -> str:
    return "\n".join(line for line in source.splitlines() if not _COMMENT.match(line))


def _decode(data: bytes) -> str:
    """Detection reads text; a file that will not decode simply is not a
    candidate. Never raises -- a binary file named .tex must not break the
    import of the project around it."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return ""


def detect_main(candidates: Sequence[tuple[str, bytes]]) -> str:
    """The path of the file to compile.

    Order: the only `.tex` declaring a document class; else a preferred name
    at the ROOT; else the shallowest declaring file when that is unique.
    Anything still tied raises rather than choosing.
    """
    declaring = [
        path
        for path, data in candidates
        if path.endswith(".tex") and "\\documentclass" in _uncommented(_decode(data))
    ]
    if not declaring:
        raise NoMainFile()
    if len(declaring) == 1:
        return declaring[0]

    for name in _PREFERRED_ROOT_NAMES:
        if name in declaring:
            return name

    depths = {p: p.count("/") for p in declaring}
    shallowest = min(depths.values())
    at_top = [p for p, d in depths.items() if d == shallowest]
    if len(at_top) == 1:
        return at_top[0]

    raise AmbiguousMain(declaring)


def detect_engine(source: str) -> str:
    """`xelatex` when the source needs it, else `pdflatex`."""
    body = _uncommented(source)
    return "xelatex" if any(pkg in body for pkg in _XETEX_PACKAGES) else "pdflatex"
