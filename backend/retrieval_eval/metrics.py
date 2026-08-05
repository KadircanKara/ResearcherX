"""Pure scoring for the retrieval eval harness.

Everything here is a function of already-fetched chunks, so the metrics are
unit-testable without Postgres or an embedding provider. The runner does one
query per case and feeds the full chunk list to these functions — the sweep
therefore costs no extra queries.
"""

from __future__ import annotations

from dataclasses import dataclass

from retrieval_eval.golden_set import Case, chunk_satisfies

# Swept range. Below 0.30 nothing survives; above 0.90 nothing is excluded.
THRESHOLDS: tuple[float, ...] = tuple(round(0.30 + 0.05 * i, 2) for i in range(13))


@dataclass(frozen=True)
class Scored:
    paper_title: str
    chunk_text: str
    distance: float


@dataclass(frozen=True)
class SweepRow:
    threshold: float
    content_recall: float
    off_topic_false_accept: float


def simulate_retrieval(chunks: list[Scored], k: int) -> list[Scored]:
    """Reproduce chat_service._retrieve_paper_chunks: top-k PER PAPER, concatenated.

    Not a global top-k — the production path queries each paper separately, so a
    global cut would starve every paper but the nearest and overstate misses.
    """
    by_paper: dict[str, list[Scored]] = {}
    for chunk in sorted(chunks, key=lambda c: c.distance):
        by_paper.setdefault(chunk.paper_title, []).append(chunk)
    kept = [c for group in by_paper.values() for c in group[:k]]
    return sorted(kept, key=lambda c: c.distance)


def first_satisfying_rank(case: Case, retrieved: list[Scored]) -> int | None:
    """1-based rank of the first correct chunk, or None if there isn't one."""
    for rank, chunk in enumerate(retrieved, start=1):
        if chunk_satisfies(case, chunk.paper_title, chunk.chunk_text):
            return rank
    return None


def recall_at_k(case_chunks: list[tuple[Case, list[Scored]]], k: int) -> float:
    if not case_chunks:
        return 0.0
    hits = sum(
        1
        for case, chunks in case_chunks
        if first_satisfying_rank(case, simulate_retrieval(chunks, k)) is not None
    )
    return hits / len(case_chunks)


def mean_reciprocal_rank(case_chunks: list[tuple[Case, list[Scored]]], k: int) -> float:
    if not case_chunks:
        return 0.0
    total = 0.0
    for case, chunks in case_chunks:
        rank = first_satisfying_rank(case, simulate_retrieval(chunks, k))
        if rank is not None:
            total += 1.0 / rank
    return total / len(case_chunks)


def best_satisfying_distance(case: Case, chunks: list[Scored]) -> float | None:
    """Closest distance among correct chunks — the raw material for tuning."""
    hits = [c.distance for c in chunks if chunk_satisfies(case, c.paper_title, c.chunk_text)]
    return min(hits) if hits else None


def noise_floor(negative_chunks: list[list[Scored]]) -> float | None:
    """Closest distance any off-topic question achieved against any chunk.

    A threshold is only meaningful if it sits below this and above the content
    cases' best distances.
    """
    bests = [min(c.distance for c in chunks) for chunks in negative_chunks if chunks]
    return min(bests) if bests else None


def sweep(
    positives: list[tuple[Case, list[Scored]]],
    negatives: list[list[Scored]],
    k: int,
    thresholds: tuple[float, ...] = THRESHOLDS,
) -> list[SweepRow]:
    """Content recall vs off-topic false-accept at each candidate cutoff."""
    rows: list[SweepRow] = []
    for threshold in thresholds:
        kept_pos = [
            (case, [c for c in chunks if c.distance < threshold]) for case, chunks in positives
        ]
        recall = recall_at_k(kept_pos, k)

        if negatives:
            accepted = sum(1 for chunks in negatives if any(c.distance < threshold for c in chunks))
            false_accept = accepted / len(negatives)
        else:
            false_accept = 0.0
        rows.append(
            SweepRow(
                threshold=threshold, content_recall=recall, off_topic_false_accept=false_accept
            )
        )
    return rows


def separating_threshold(rows: list[SweepRow]) -> float | None:
    """Lowest cutoff with full content recall and zero off-topic acceptance.

    None means no cutoff separates the two populations — which is a finding,
    not a failure: it says an absolute cosine cutoff is the wrong instrument
    for this model, and the next lever is reranking or hybrid retrieval.
    """
    for row in rows:
        if row.content_recall == 1.0 and row.off_topic_false_accept == 0.0:
            return row.threshold
    return None
