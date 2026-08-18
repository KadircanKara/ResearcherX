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

    When floor * len(scope) exceeds budget, the pin loop saturates mid-round
    and deterministically favours papers earlier in `scope`.

    The function is defensive about a duplicated paper id in `scope` — if the
    same id appears twice, it is treated as a single entry.
    """
    if floor <= 0:
        return list(ordered[:budget])

    # Dedupe scope, order-preserving, so a duplicate id cannot cause duplication
    scope = list(dict.fromkeys(scope))

    per_paper: dict[str, list[T]] = {paper_id: [] for paper_id in scope}
    for item in ordered:
        bucket = per_paper.get(paper_of(item))
        if bucket is not None:
            bucket.append(item)

    pinned_ids: set[int] = set()
    pinned: list[T] = []
    for rank in range(floor):
        for paper_id in scope:
            items = per_paper.get(paper_id) or []
            if rank < len(items) and len(pinned) < budget:
                item = items[rank]
                pinned.append(item)
                pinned_ids.add(id(item))

    kept = list(pinned)
    for item in ordered:
        if len(kept) >= budget:
            break
        if id(item) not in pinned_ids:
            kept.append(item)
            pinned_ids.add(id(item))
    return kept
