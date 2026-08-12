"""Chunk selection — how many chunks to take, and from which papers.

When retrieval is scoped to ONE paper, the user has already named the paper,
so the question is no longer "is this chunk relevant enough to beat other
papers?" but "which of THIS paper's chunks answer the question?". An absolute
cosine cutoff answers the first question; this module answers the second.

The policy is a relative cut: keep every chunk within `delta` of the paper's
own nearest chunk. It is deliberately NOT the whole safety story — measured on
the live corpus, an off-topic question has a compressed distance spread
(best 0.7518-0.8581), so a relative rule on its own keeps 24/24, 44/44 and
64/64 chunks of the nearest paper. The absolute ceiling that blocks that lives
in the SQL (`settings.intra_paper_ceiling`); this function assumes it already
ran. Ceiling = noise floor, delta = cost and precision.

MULTI-paper scope adds two more decisions, both here for the same reason:
`evals/retrieval/run_eval.py` imports these exact functions, so the harness
can never measure a policy production does not run. `admit_papers` decides
WHICH papers contribute at all; `merge_across_papers` decides how the
per-paper cuts share one fixed budget. Neither may move into SQL.
"""

from collections.abc import Mapping, Sequence


def keep_within_paper(distances: Sequence[float], *, delta: float) -> int:
    """How many chunks to keep from a distance-ASCENDING list.

    Returns a PREFIX LENGTH, not a filtered list: the caller holds the rows
    (with their text, paper_id and chunk_index) and slices them itself, which
    keeps this function free of any DB or ORM type.

    Stops at the first distance beyond `best + delta` rather than filtering
    the whole list. On sorted input the two are identical; on unsorted input
    stopping is the safer of the two, since a chunk sitting behind an excluded
    one has already lost to it.
    """
    if not distances:
        return 0
    limit = distances[0] + delta
    kept = 0
    for distance in distances:
        if distance > limit:
            break
        kept += 1
    return kept


def admit_papers(
    best_by_paper: Mapping[str, float], *, threshold: float
) -> tuple[list[str], list[str]]:
    """Split resolved papers into (admitted, rejected) on their nearest chunk.

    `threshold` is `settings.similarity_threshold` — the number already tuned
    for INTER-paper discrimination, reused rather than duplicated.

    Known and accepted: this rejects the off_topic class (best chunk
    0.7518-0.8581 on the golden set) but ADMITS near-domain negatives
    (0.5474-0.6521). Near-domain containment is the resolver's job plus
    `per_paper_floor`, not this gate's — see app/core/config.py.

    Admitted come back sorted nearest-best-chunk first, which is the order
    `merge_across_papers` round-robins in, so a tie in the floor phase favours
    the more relevant paper.
    """
    admitted = sorted(
        (pid for pid, best in best_by_paper.items() if best < threshold),
        key=lambda pid: best_by_paper[pid],
    )
    admitted_set = set(admitted)
    rejected = [pid for pid in best_by_paper if pid not in admitted_set]
    return admitted, rejected


def merge_across_papers(
    ranked: Mapping[str, Sequence[float]], *, budget: int, floor: int
) -> dict[str, int]:
    """How many chunks to take from each paper, as a PREFIX LENGTH.

    Returns prefix lengths rather than chunks for the same reason
    `keep_within_paper` returns a count: the caller holds the rows with their
    text, paper_id and chunk_index, which keeps this module free of any DB or
    ORM type.

    Two phases:
      1. round-robin across papers (nearest best chunk first) until each holds
         `floor` chunks or is exhausted — this is the representation
         guarantee, and it is the whole reason a per-paper cut alone does not
         fix asymmetric evidence: if A's cut keeps 55 and B's keeps 20, a
         global sort still hands A all 60 slots whenever every one of B's
         chunks is farther than A's 55th;
      2. fill whatever budget remains from the untaken chunks by global
         distance, so a genuinely more relevant paper still goes deeper.

    `ranked` values must be distance-ASCENDING (each paper's own cut, already
    delta-trimmed). Phase 2 relies on it: taking globally-sorted leftovers
    then extends each paper's prefix in order, never leaving a hole.
    """
    counts = {paper_id: 0 for paper_id in ranked}
    if not ranked or budget <= 0:
        return counts

    remaining = budget
    for _ in range(floor):
        if remaining == 0:
            break
        for paper_id in ranked:
            if remaining == 0:
                break
            if counts[paper_id] < len(ranked[paper_id]):
                counts[paper_id] += 1
                remaining -= 1

    if remaining:
        leftovers = sorted(
            (distance, paper_id)
            for paper_id, distances in ranked.items()
            for distance in distances[counts[paper_id] :]
        )
        for _, paper_id in leftovers[:remaining]:
            counts[paper_id] += 1
    return counts
