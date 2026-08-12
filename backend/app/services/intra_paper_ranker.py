"""Intra-paper chunk selection.

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

Pure on purpose: `evals/retrieval/run_eval.py --targeted` imports this exact
function, so the harness can never measure a policy production does not run.
"""

from collections.abc import Sequence


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
