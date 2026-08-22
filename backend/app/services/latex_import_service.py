"""Turning a validated archive into a document and its tree.

ONE TRANSACTION. The document row and every file land together or not at all
-- a rejected archive must not leave an empty document behind, the same
failure plan 1 fixed in `create_document`.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LatexDocument
from app.services import latex_dedupe
from app.services import latex_files_service as files
from app.services.latex_archive import ArchiveEntry
from app.services.latex_detect import detect_engine, detect_main
from app.services.latex_paths import MANIFEST_PATH

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
    """Best-effort only. ANY problem -- malformed JSON, wrong shape, deeply
    nested/pathological JSON, an engine that isn't one of the two real
    values, a main_path that fails its guard -- degrades to "manifest
    absent", never raises. The manifest is read from a zip entry an
    attacker fully controls, so this is a hostile-input boundary like
    `parse_structured`: `json.loads` on attacker JSON can raise more than
    `JSONDecodeError` (a `[` repeated deeply enough blows the C parser's
    recursion limit with a bare `RecursionError`), and a value read out of
    the parsed object can be any JSON type, not just the expected one (an
    `engine` of `["xelatex"]` or `{}` is unhashable and must never reach an
    `in <frozenset>` check un-type-checked)."""
    try:
        obj = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError, ValueError):
        return None, None
    if not isinstance(obj, dict):
        return None, None
    main_path = _manifest_main_path(obj.get("main_path"), entries)
    engine = obj.get("engine")
    engine = engine if isinstance(engine, str) and engine in _VALID_ENGINES else None
    return main_path, engine


def _split_manifest(
    entries: list[ArchiveEntry],
) -> tuple[list[ArchiveEntry], str | None, str | None]:
    """The entries that will land in the tree, plus whatever the manifest
    decided. The manifest is consumed, never written."""
    manifest_source = next((e for e in entries if e.path == MANIFEST_PATH), None)
    kept = _without_manifest(entries)
    if manifest_source is None:
        return kept, None, None
    manifest_main, manifest_engine = _parse_manifest(manifest_source.data, kept)
    return kept, manifest_main, manifest_engine


def detect_main_for(entries: list[ArchiveEntry], *, override: str | None = None) -> str:
    """Which file this archive would compile, deciding nothing else.

    Split out of `import_archive` so the `import/plan` step can ask whether
    the main file is undecidable WITHOUT creating anything -- the logic
    therefore exists exactly once. Raises `AmbiguousMain` / `NoMainFile`
    just as detection does.

    Precedence: explicit override > manifest > detection.
    """
    kept, manifest_main, _ = _split_manifest(entries)
    # detect_main must never see a binary file: it judges candidacy by
    # decodability, but a .tex file containing a NUL decodes cleanly while
    # still being unusable as source (Finding 2).
    return (
        override
        or manifest_main
        or detect_main([(e.path, e.data) for e in kept if not e.is_binary])
    )


async def import_archive(
    db: AsyncSession,
    *,
    project_id: str,
    user_id: str,
    entries: list[ArchiveEntry],
    name: str,
    main_path: str | None = None,
    renames: dict[str, str] | None = None,
) -> tuple[LatexDocument, int]:
    """Create the document and write every entry. Caller commits.

    `main_path` overrides detection -- that is how the client answers an
    AmbiguousMain 422. It must name an entry in THIS archive and pass the
    caller's own `.tex`/text guard; the caller checks both before calling.

    Precedence for both `main_path` and `engine`: explicit query override >
    manifest > detection.

    `renames` maps an archive path to the path it should land at. A create
    cannot collide with an existing tree, but the user's decisions still
    reach here -- and the stored `main_path` follows its own rename, or the
    document would point at a file the tree does not contain.

    A rename naming the MANIFEST entry itself is silently inert: the
    manifest is stripped by `_split_manifest` after `renames` is applied to
    everything else, so it can neither land in the tree under a new name nor
    be resurrected by a decision. That is deliberate -- the manifest is
    consumed metadata, not one of the user's files.
    """
    renames = renames or {}
    # `detect_main_for` is handed the ORIGINAL entries, manifest included --
    # it is what reads the manifest's `main_path`, and stripping the
    # manifest first would silently demote every exported project back to
    # detection and re-raise the AmbiguousMain the manifest exists to answer.
    chosen = detect_main_for(entries, override=main_path)
    entries, _, manifest_engine = _split_manifest(entries)
    main = next(e for e in entries if e.path == chosen)
    engine = manifest_engine or detect_engine(main.data.decode("utf-8", errors="replace"))

    document = LatexDocument(
        project_id=project_id,
        name=name,
        main_path=renames.get(chosen, chosen),
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
        db, document.id, [(renames.get(e.path, e.path), e.data, e.is_binary) for e in entries]
    )
    return document, count


def _without_manifest(entries: list[ArchiveEntry]) -> list[ArchiveEntry]:
    """The manifest is consumed by import and never lands in the tree, so it
    can neither collide nor be reported as colliding."""
    return [e for e in entries if e.path != MANIFEST_PATH]


def landing_paths(entries: list[ArchiveEntry], renames: dict[str, str] | None = None) -> list[str]:
    """The paths this archive would actually occupy, decisions applied.

    The manifest is excluded because it is consumed, never written -- so
    "how many files will this add" and "do the resolved paths collide with
    each other" are both asked of this list rather than of `entries`.
    """
    renames = renames or {}
    return [renames.get(e.path, e.path) for e in _without_manifest(entries)]


def plan_merge(taken: Sequence[str], entries: list[ArchiveEntry]) -> list[latex_dedupe.Collision]:
    """Which archive entries would collide with the tree they are merging
    into. Pure -- the caller supplies the taken paths."""
    return latex_dedupe.plan_writes([e.path for e in _without_manifest(entries)], taken)


async def merge_archive(
    db: AsyncSession,
    *,
    document_id: str,
    entries: list[ArchiveEntry],
    renames: dict[str, str],
) -> int:
    """Add an archive's files to an EXISTING document. Caller commits.

    `renames` maps an archive path to the path it should land at -- the
    user's resolved decisions. An entry with no rename lands at its own path.

    The document's own `main_path` and `engine` are untouched, deliberately:
    a merge adds files, and the archive's idea of which file is main has no
    authority over a document that already compiles.
    """
    kept = _without_manifest(entries)
    return await files.bulk_merge(
        db,
        document_id,
        [(renames.get(e.path, e.path), e.data, e.is_binary) for e in kept],
    )
