"""One summary record per paper, written after ingest and by the backfill.

`write_record` is the ONLY writer of `paper_summaries`: the live path
(`apply_summary`, scheduled by ingest) and the batch backfill both call it,
so every row, backfilled or live, gets the same fields and a doc embedding.

The summary is never load-bearing. It runs after the paper is `ready`, in
its own sessions, as an unawaited task registered under
`summary:<paper_id>`, and it never raises (a cancellation still does, so
shutdown can stop it). A task killed by shutdown leaves the paper with no
row, or a ready row without an embedding; the backfill picks it up.
"""

import asyncio

from sqlalchemy import insert, null, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import log
from app.db.models import Paper, PaperStatus, PaperSummary
from app.db.session import SessionLocal
from app.services.embedding_service import EmbeddingService, vector_literal
from app.services.paper_summary_client import GeneratedSummary, generate_summary
from app.services.paper_summary_prompt import SUMMARY_VER, embed_text, full_text
from app.services.task_registry import registry

READY = "ready"
FAILED = "failed"
SOURCE_LLM = "llm"
# apply_summary's outcomes besides a row status.
SKIPPED = "skipped"  # paper missing, or not ready
ERROR = "error"  # the work raised; nothing (or only the summary) was written

_NO_TEXT = "no summary was generated and the paper has no abstract"
_ERROR_MAX = 300

_embedding_svc = EmbeddingService()


async def write_record(
    db: AsyncSession,
    paper: Paper,
    generated: GeneratedSummary | None,
    *,
    source_if_none: str = "abstract",
) -> str:
    """Upsert `paper`'s summary row, then embed it. Returns the row status.

    No `generated` record falls back to the abstract (`source_if_none`), and
    with no abstract either the row is `failed`. Two commits: the summary
    first, then the doc embedding, so an embedding error cannot lose the
    summary; it leaves the row `ready` with no embedding and is logged.
    Raises only if the summary write itself fails.
    """
    paper_id, title, abstract = paper.id, paper.title, paper.abstract
    if generated is not None:
        summary, keywords = generated.record.summary, list(generated.record.keywords)
        summary_model, source, status, error = generated.model, SOURCE_LLM, READY, None
    elif abstract and abstract.strip():
        summary, keywords = abstract.strip(), []
        summary_model, source, status, error = None, source_if_none, READY, None
    else:
        summary, keywords = None, []
        summary_model, source, status, error = None, None, FAILED, _NO_TEXT

    # Core statements, never an ORM flush: `doc_embedding` is Text on the
    # model and vector(768) in Postgres, and asyncpg binds any ORM value for
    # it (NULL included) with a ::VARCHAR cast the vector column rejects. So
    # it is never bound, only cleared as a literal NULL: the old vector
    # described the old summary.
    values = {
        "summary": summary,
        "keywords": keywords,
        "summary_model": summary_model,
        "source": source,
        "status": status,
        "summary_ver": SUMMARY_VER,
        "error": error,
        "batch_id": None,
        "model": "",
    }
    exists = (
        await db.execute(select(PaperSummary.paper_id).where(PaperSummary.paper_id == paper_id))
    ).first()
    if exists is None:
        await db.execute(insert(PaperSummary).values(paper_id=paper_id, **values))
    else:
        await db.execute(
            update(PaperSummary)
            .where(PaperSummary.paper_id == paper_id)
            .values(doc_embedding=null(), **values)
        )
    await db.commit()

    if status == READY:
        await _write_embedding(db, paper_id, embed_text(title, abstract, summary))
    return status


async def _write_embedding(db: AsyncSession, paper_id: str, doc: str) -> None:
    """Second commit: the doc embedding, recorded with the model that made it."""
    try:
        vec = await _embedding_svc.embed(doc, task_type="RETRIEVAL_DOCUMENT")
        # Raw SQL with an explicit cast, as for chunk embeddings: asyncpg
        # rejects a varchar bind against vector(768). sqlite (tests) keeps
        # the column as Text, where a cast to an unknown type would coerce.
        emb = "CAST(:emb AS vector(768))" if db.get_bind().dialect.name == "postgresql" else ":emb"
        await db.execute(
            text(
                f"UPDATE paper_summaries SET doc_embedding = {emb}, model = :model "
                "WHERE paper_id = :paper_id"
            ),
            {"emb": vector_literal(vec), "model": settings.embedding_model, "paper_id": paper_id},
        )
        await db.commit()
    except Exception as exc:  # noqa: BLE001 -- the summary is already stored
        await db.rollback()
        log.warning(
            "paper_summary_embedding_failed",
            paper_id=paper_id,
            error=f"{type(exc).__name__}: {str(exc)[:_ERROR_MAX]}",
        )


async def _ready_paper(db: AsyncSession, paper_id: str) -> Paper | None:
    paper = await db.get(Paper, paper_id)
    return paper if paper is not None and paper.status == PaperStatus.READY else None


async def apply_summary(paper_id: str) -> str:
    """Generate and store one paper's summary. Never raises.

    Returns the row status (`ready` / `failed`), `skipped` when the paper is
    gone or not ready, or `error` when the work raised. Catches `Exception`
    only, so a cancellation propagates. The paper is read in one session and
    written in another, so no pooled connection is held across the LLM call.
    """
    try:
        async with SessionLocal() as db:
            paper = await _ready_paper(db, paper_id)
            if paper is None:
                log.info("paper_summary_skipped", paper_id=paper_id)
                return SKIPPED
            title, abstract = paper.title, paper.abstract
            source_text = full_text(
                paper.extracted_pages,
                paper.body,
                abstract,
                cap=settings.paper_summary_input_chars,
            )

        generated = await generate_summary(title, abstract, source_text)

        async with SessionLocal() as db:
            # Re-checked: the paper may have been deleted (or re-ingested)
            # while the model was writing.
            paper = await _ready_paper(db, paper_id)
            if paper is None:
                log.info("paper_summary_skipped", paper_id=paper_id, after_generation=True)
                return SKIPPED
            status = await write_record(db, paper, generated)
    except Exception as exc:  # noqa: BLE001 -- a summary must never fail anything
        log.warning(
            "paper_summary_apply_failed",
            paper_id=paper_id,
            error=f"{type(exc).__name__}: {str(exc)[:_ERROR_MAX]}",
        )
        return ERROR

    log.info(
        "paper_summary_done",
        paper_id=paper_id,
        status=status,
        source=SOURCE_LLM if generated is not None else "fallback",
        summary_model=generated.model if generated else None,
        input_tokens=generated.input_tokens if generated else 0,
        output_tokens=generated.output_tokens if generated else 0,
        cost_usd=generated.cost_usd if generated else None,
    )
    return status


async def needs_summary(db: AsyncSession, paper_id: str) -> bool:
    """True when the paper has no summary row, or one that is not ready, or
    one written by an older prompt version. One cheap query."""
    row = (
        await db.execute(
            select(PaperSummary.status, PaperSummary.summary_ver).where(
                PaperSummary.paper_id == paper_id
            )
        )
    ).first()
    return row is None or row.status != READY or row.summary_ver < SUMMARY_VER


def schedule_summary(paper_id: str) -> None:
    """Start `apply_summary` in the background, at most one per paper.

    A no-op while a task for this paper is still running. Not called from
    scripts: their process exits before the task finishes; the backfill
    covers those papers.
    """
    key = f"summary:{paper_id}"
    if registry.is_running(key):
        log.info("paper_summary_already_running", paper_id=paper_id)
        return
    registry.register(key, asyncio.create_task(apply_summary(paper_id)))
