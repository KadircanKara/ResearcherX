"""Turning a validated archive into a document and its tree.

ONE TRANSACTION. The document row and every file land together or not at all
-- a rejected archive must not leave an empty document behind, the same
failure plan 1 fixed in `create_document`.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LatexDocument
from app.services import latex_files_service as files
from app.services.latex_archive import ArchiveEntry
from app.services.latex_detect import detect_engine, detect_main


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
    """
    chosen = main_path or detect_main([(e.path, e.data) for e in entries])
    main = next(e for e in entries if e.path == chosen)
    engine = detect_engine(main.data.decode("utf-8", errors="replace"))

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
