"""Command line for the local RAG system.

    python -m app.local_rag.cli ingest paper1.pdf notes.md [--root DIR]
    python -m app.local_rag.cli ask "what is revisit time?" [--top-n 10] [--no-answer]

`-m` is required, as for every other script in this repo: `pyproject.toml`
packages only `app*`, so file-path invocation drops the working directory
from `sys.path`.

Ingest and query both need an embedding endpoint (`EMBEDDING_*`); only the
answer step needs `LLM_*`, and only the rerank step needs `COHERE_API_KEY`
— without it retrieval still runs, in fused RRF order.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.core.config import settings
from app.core.logging import configure_logging
from app.local_rag.ingest import ingest
from app.local_rag.rag import answer, retrieve
from app.local_rag.search import SearchParams
from app.services.embedding_service import EmbeddingService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="local-rag", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    ingest_cmd = sub.add_parser("ingest", help="build the index from PDFs or text files")
    ingest_cmd.add_argument("sources", nargs="+")
    ingest_cmd.add_argument("--root", default=settings.local_rag_dir)

    ask = sub.add_parser("ask", help="retrieve, then answer")
    ask.add_argument("query")
    ask.add_argument("--root", default=settings.local_rag_dir)
    ask.add_argument("--top-n", type=int, default=SearchParams().rerank_top_n)
    ask.add_argument("--candidates", type=int, default=SearchParams().rerank_candidates)
    ask.add_argument("--threshold", type=float, default=SearchParams().similarity_threshold)
    ask.add_argument("--no-answer", action="store_true", help="show the retrieved excerpts only")
    return parser


def params_from_args(args: argparse.Namespace) -> SearchParams:
    return SearchParams(
        similarity_threshold=args.threshold,
        rerank_candidates=args.candidates,
        rerank_top_n=args.top_n,
    )


async def _run_ingest(args: argparse.Namespace) -> None:
    embeddings = EmbeddingService()

    async def embed(texts: list[str]) -> list[list[float]]:
        return await embeddings.embed_batch(texts, task_type="RETRIEVAL_DOCUMENT")

    report = await ingest(
        sources=[Path(s) for s in args.sources], root=Path(args.root), embed=embed
    )
    print(f"indexed {report.n_papers} paper(s), {report.n_chunks} chunk(s) -> {report.root}")


async def _run_ask(args: argparse.Namespace) -> None:
    hits = await retrieve(root=Path(args.root), query=args.query, params=params_from_args(args))
    if not hits:
        print("no excerpt cleared the gate — nothing to answer from")
        return

    for hit in hits:
        arms = f"dense={hit.dense_rank} sparse={hit.sparse_rank}"
        score = "" if hit.rerank_score is None else f" rerank={hit.rerank_score:.3f}"
        print(f"[{hit.n}] {hit.chunk.paper_title} #{hit.chunk.chunk_index}  {arms}{score}")
        print(f"    {hit.chunk.text[:160].strip()}...")
    if args.no_answer:
        return

    print("\n" + "-" * 60 + "\n")
    print(await answer(root=Path(args.root), query=args.query, hits=hits))


def main() -> None:
    configure_logging()
    args = build_parser().parse_args()
    asyncio.run(_run_ingest(args) if args.command == "ingest" else _run_ask(args))


if __name__ == "__main__":
    main()
