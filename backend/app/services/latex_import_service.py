"""Turning a validated archive into a document and its tree.

ONE TRANSACTION. The document row and every file land together or not at all
-- a rejected archive must not leave an empty document behind, the same
failure plan 1 fixed in `create_document`.
"""

from __future__ import annotations

import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LatexDocument
from app.services import latex_files_service as files
from app.services.latex_archive import ArchiveEntry
from app.services.latex_detect import detect_engine, detect_main

# Written by our own /export, consumed (and discarded) by /import so a round
# trip preserves the two decisions detection cannot re-derive on its own: an
# ambiguous main_path the user already resolved, and an engine set by PATCH
# without a triggering package in the source. Never appears in the imported
# tree, `file_count`, or the document's quota -- it describes the export, it
# is not part of it.
MANIFEST_PATH = ".researcherx.json"

_VALID_ENGINES = frozenset({"pdflatex", "xelatex"})


def _manifest_main_path(raw: object, entries: list[ArchiveEntry]) -> str | None:
    """The same guard the explicit `?main_path=` query override gets in the
    route: present in the archive, `.tex`, not binary. A manifest is
    attacker-supplied like every other archive byte -- failing this check
    means falling back to detection, never a 422 the user can't explain."""
    if not isinstance(raw, str):
        return None
    match = next((e for e in entries if e.path == raw), None)
    if match is None or match.is_binary or not match.path.endswith(".tex"):
        return None
    return raw


def _parse_manifest(data: bytes, entries: list[ArchiveEntry]) -> tuple[str | None, str | None]:
    """Best-effort only. ANY problem -- malformed JSON, wrong shape, an
    engine that isn't one of the two real values, a main_path that fails its
    guard -- degrades to "manifest absent", never raises. The manifest is
    read from a zip entry an attacker fully controls."""
    try:
        obj = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, None
    if not isinstance(obj, dict):
        return None, None
    main_path = _manifest_main_path(obj.get("main_path"), entries)
    engine = obj.get("engine")
    engine = engine if engine in _VALID_ENGINES else None
    return main_path, engine


async def import_archive(
    db: AsyncSession,
    *,
    project_id: str,
    user_id: str,
    entries: list[ArchiveEntry],
    name: str,
    main_path: str | None = None,
) -> tuple[LatexDocument, int]:
    """Create the document and write every entry. Caller commits.

    `main_path` overrides detection -- that is how the client answers an
    AmbiguousMain 422. It must name an entry in THIS archive and pass the
    caller's own `.tex`/text guard; the caller checks both before calling.

    Precedence for both `main_path` and `engine`: explicit query override >
    manifest > detection.
    """
    manifest_source = next((e for e in entries if e.path == MANIFEST_PATH), None)
    entries = [e for e in entries if e.path != MANIFEST_PATH]

    manifest_main, manifest_engine = (
        _parse_manifest(manifest_source.data, entries)
        if manifest_source is not None
        else (None, None)
    )

    # detect_main must never see a binary file: it judges candidacy by
    # decodability, but a .tex file containing a NUL decodes cleanly while
    # still being unusable as source (Finding 2).
    chosen = (
        main_path
        or manifest_main
        or detect_main([(e.path, e.data) for e in entries if not e.is_binary])
    )
    main = next(e for e in entries if e.path == chosen)
    engine = manifest_engine or detect_engine(main.data.decode("utf-8", errors="replace"))

    document = LatexDocument(
        project_id=project_id,
        name=name,
        main_path=chosen,
        engine=engine,
        created_by=user_id,
    )
    db.add(document)
    await db.flush()  # populate id WITHOUT committing

    # `bulk_create` rather than a per-entry write_text/write_binary loop: the
    # tree is guaranteed empty (this document was just created), so the
    # per-write collision scan and quota SUM those functions perform are
    # vacuous here and cost O(n^2) database round trips on a large import.
    count = await files.bulk_create(
        db, document.id, [(e.path, e.data, e.is_binary) for e in entries]
    )
    return document, count
