"""Per-paper floor for explicitly mentioned papers.

Pure, no DB and no ORM types, matching the intra_paper_ranker.py /
hybrid_ranker.py convention: the eval harness imports these functions directly,
so a policy production runs can never differ from the policy measured. Pushing
this into SQL would break that guarantee.
"""

from collections.abc import Callable, Sequence
from typing import TypeVar

T = TypeVar("T")


def apply_per_paper_floor(
    ordered: Sequence[T],
    *,
    paper_of: Callable[[T], str],
    scope: Sequence[str],
    floor: int,
    budget: int,
) -> list[T]:
    """Guarantee each scoped paper `floor` items, then fill by relevance.

    `ordered` is the ranking (nearest/fused first). The floor is taken
    round-robin so the guarantee is spread across papers rather than granted to
    whichever paper the ranking already favoured, and each paper's floor items
    are its OWN best — relative order inside a paper is never disturbed, since
    the caller numbers citations over the final order.

    A paper with fewer items than the floor contributes what it has; the unused
    slots return to the distance fill rather than being held open for chunks
    that do not exist. A scoped paper with no items at all is simply absent —
    the SCOPE block still names it, so the model can say so.

    `budget` is a hard ceiling and is applied last: the floor may only change
    WHICH items are kept, never how many.
    """
    if floor <= 0:
        return list(ordered[:budget])

    per_paper: dict[str, list[T]] = {paper_id: [] for paper_id in scope}
    for item in ordered:
        bucket = per_paper.get(paper_of(item))
        if bucket is not None:
            bucket.append(item)

    pinned: list[T] = []
    for rank in range(floor):
        for paper_id in scope:
            items = per_paper.get(paper_id) or []
            if rank < len(items) and len(pinned) < budget:
                pinned.append(items[rank])

    pinned_ids = {id(item) for item in pinned}
    kept = list(pinned)
    for item in ordered:
        if len(kept) >= budget:
            break
        if id(item) not in pinned_ids:
            kept.append(item)
    return kept
