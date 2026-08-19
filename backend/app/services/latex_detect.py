"""Choosing the main file and the engine for an imported project.

PURE -- no DB, no ORM, no service imports.

Detection NEVER picks between equals. `paper_resolver.py` holds the same line
for the same reason: an LLM targeter that guessed a paper measured worse than
no targeter at all, because a confident wrong answer is acted on while an
honest question is answered. A wrong `main_path` compiles the wrong document
and the user has no way to see why.

Known, deliberate limitation: inline `\\verb|...|` spans are NOT recognized as
inactive. Handling them correctly needs a real tokenizer; a partial regex
attempt would be worse than doing nothing, since it would silently mis-parse
some cases while looking handled. Only block environments (`verbatim`,
`lstlisting`, `minted`) are stripped.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

# Only these need a Unicode engine, and each hard-FAILS under pdflatex -- so
# defaulting to pdflatex guarantees a broken first compile on exactly the
# projects most likely to arrive from Overleaf.
_XETEX_PACKAGES = ("fontspec", "unicode-math", "polyglossia")

_PREFERRED_ROOT_NAMES = ("main.tex", "paper.tex", "ms.tex")

# Block environments whose contents are never live LaTeX -- a
# \documentclass or \usepackage line inside one of these is example text,
# not a real declaration.
_INACTIVE_ENVS = ("verbatim", "lstlisting", "minted")

# \usepackage / \RequirePackage, an optional [options] group, then a
# required {names,...} group. Package names are matched as EXACT,
# comma-split tokens against _XETEX_PACKAGES so "fontspec-xyz" or
# "nofontspec" cannot false-positive on a substring match.
_PACKAGE_CMD = re.compile(r"\\(?:usepackage|RequirePackage)(?:\s*\[[^\]]*\])?\s*\{([^}]*)\}")


class AmbiguousMain(Exception):
    def __init__(self, paths: list[str]) -> None:
        super().__init__(f"several files could be the main file: {', '.join(sorted(paths))}")
        self.paths = sorted(paths)


class NoMainFile(Exception):
    def __init__(self) -> None:
        super().__init__("no LaTeX main file found in the archive")


def _strip_line_comment(line: str) -> str:
    """Drop everything from the first UNESCAPED `%` onward. `\\%` is a literal
    percent, not a comment start -- a run of backslashes immediately before
    `%` only escapes it when that run's length is odd (each `\\\\` pair
    escapes itself, leaving the last one to escape the `%`)."""
    backslashes = 0
    for i, ch in enumerate(line):
        if ch == "\\":
            backslashes += 1
            continue
        if ch == "%" and backslashes % 2 == 0:
            return line[:i]
        backslashes = 0
    return line


def _strip_inactive(source: str) -> str:
    """Remove line comments and the bodies of verbatim-like block
    environments, so neither can nominate a main file or select an engine.
    A commented-out `\\begin{verbatim}` does not open a block (checked
    against the already-comment-stripped line)."""
    out_lines: list[str] = []
    in_env: str | None = None
    for line in source.splitlines():
        if in_env is not None:
            if f"\\end{{{in_env}}}" in line:
                in_env = None
            continue
        stripped = _strip_line_comment(line)
        opened = None
        for env in _INACTIVE_ENVS:
            if f"\\begin{{{env}}}" in stripped:
                opened = env
                break
        if opened is not None:
            in_env = opened
            continue
        out_lines.append(stripped)
    return "\n".join(out_lines)


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

    Order: the only `.tex` declaring a document class; else the single
    preferred name at the ROOT; else the shallowest declaring file when
    that is unique. Anything still tied -- including two different
    preferred root names both declaring -- raises rather than choosing.
    """
    declaring = [
        path
        for path, data in candidates
        if path.endswith(".tex") and "\\documentclass" in _strip_inactive(_decode(data))
    ]
    if not declaring:
        raise NoMainFile()
    if len(declaring) == 1:
        return declaring[0]

    preferred_present = [name for name in _PREFERRED_ROOT_NAMES if name in declaring]
    if len(preferred_present) == 1:
        return preferred_present[0]
    if len(preferred_present) > 1:
        raise AmbiguousMain(preferred_present)

    depths = {p: p.count("/") for p in declaring}
    shallowest = min(depths.values())
    at_top = [p for p, d in depths.items() if d == shallowest]
    if len(at_top) == 1:
        return at_top[0]

    raise AmbiguousMain(declaring)


def detect_engine(source: str) -> str:
    """`xelatex` when the source needs it, else `pdflatex`."""
    body = _strip_inactive(source)
    packages: set[str] = set()
    for match in _PACKAGE_CMD.finditer(body):
        packages.update(name.strip() for name in match.group(1).split(","))
    return "xelatex" if packages & set(_XETEX_PACKAGES) else "pdflatex"
