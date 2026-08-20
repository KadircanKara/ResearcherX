"""Path rules for files inside a LaTeX document's tree.

PURE -- no DB, no ORM, no service imports, exactly like `mention_ranker.py`
and `text_matching.py`. Every path that reaches `latex_files.path` goes
through `normalize_path` first: the write routes here, and every zip entry in
plan 2. One copy, because two copies of a traversal guard drift and the
divergence is invisible until it is exploited.

A bad path REJECTS, it is never skipped or repaired. A project silently
missing the file its \\input names compiles to a confusing TeX error a user
cannot act on; a 422 naming the entry is actionable.
"""

from __future__ import annotations

import re

# A path is stored in String(400); a segment is a filename; the depth bound
# stops a pathological tree from making every tree render O(deep) in the UI.
MAX_PATH_LENGTH = 400
MAX_NAME_LENGTH = 200
MAX_DEPTH = 16

_DRIVE = re.compile(r"^[A-Za-z]:")

# Reserved. Written by /export as the round-trip manifest (main_path/engine),
# consumed and dropped by /import -- see `latex_import_service.py`. A write
# route letting a user create a real file at this path would make export
# emit a duplicate zip member (the user's file AND the manifest, same name)
# and silently shadow/consume the user's own content on both sides of a
# round trip. Lives here, not in latex_import_service.py or
# latex_files_service.py, so both can enforce the same reservation without
# either importing the other.
MANIFEST_PATH = ".researcherx.json"


class InvalidPath(ValueError):
    """Carries the offending path so a route can name it in its 422.

    The path is the one piece of context the user needs and the one piece a
    generic "invalid archive" message throws away.
    """

    def __init__(self, path: str, reason: str) -> None:
        super().__init__(f"{path!r}: {reason}")
        self.path = path
        self.reason = reason


def normalize_path(raw: str) -> str:
    """Return the canonical relative path, or raise `InvalidPath`.

    Canonical means: forward slashes, no `.` segments, no leading `./`, and
    no trailing slash. Everything else in the reject list below is a path
    that either escapes the tree or cannot round-trip through a filesystem.
    """
    if not raw or not raw.strip():
        raise InvalidPath(raw, "empty path")
    # Checked on the RAW string, before any splitting: a control character is
    # a lie about what the path is, wherever it sits.
    if any(ord(c) < 32 or ord(c) == 127 for c in raw):
        raise InvalidPath(raw, "contains control characters")
    # Not translated to "/". A zip written with backslash separators is
    # malformed per the spec, and silently reinterpreting it would mean two
    # entries can normalize onto one path.
    if "\\" in raw:
        raise InvalidPath(raw, "backslashes are not path separators here")
    if raw.startswith("/"):
        raise InvalidPath(raw, "absolute paths are not allowed")
    if _DRIVE.match(raw):
        raise InvalidPath(raw, "drive-letter paths are not allowed")

    segments: list[str] = []
    for segment in raw.split("/"):
        if segment == ".":
            # `./x` and `a/./b` both appear in real archives and mean nothing.
            continue
        if segment == "":
            # Covers a double slash and a trailing slash. Directory entries
            # are not stored: the tree is derived from file paths.
            raise InvalidPath(raw, "empty path segment")
        if segment == "..":
            raise InvalidPath(raw, "parent-directory segments are not allowed")
        if len(segment) > MAX_NAME_LENGTH:
            raise InvalidPath(raw, f"path segment longer than {MAX_NAME_LENGTH} characters")
        segments.append(segment)

    if not segments:
        raise InvalidPath(raw, "empty path")
    if len(segments) > MAX_DEPTH:
        raise InvalidPath(raw, f"deeper than {MAX_DEPTH} directories")

    normalized = "/".join(segments)
    if len(normalized) > MAX_PATH_LENGTH:
        raise InvalidPath(raw, f"longer than {MAX_PATH_LENGTH} characters")
    return normalized


def collision_key(path: str) -> str:
    """Fold a path to the key two spellings of one file share.

    macOS and Windows filesystems are case-insensitive. A project holding
    both `Figures/Fig1.png` and `figures/fig1.png` resolves \\includegraphics
    differently depending on whose machine unpacked it, which is not a
    difference anyone can debug from the editor.
    """
    return path.lower()
