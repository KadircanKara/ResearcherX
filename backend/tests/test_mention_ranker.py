"""Per-paper floor — the guarantee that a paper the USER named is represented."""

from app.services.mention_ranker import apply_per_paper_floor


def _chunks(spec: str) -> list[tuple[str, int]]:
    """ "aab" -> [("a",0), ("a",1), ("b",2)] — items in relevance order."""
    return [(paper, i) for i, paper in enumerate(spec)]


def _papers(items: list[tuple[str, int]]) -> str:
    return "".join(paper for paper, _ in items)


def test_every_mentioned_paper_gets_its_floor_even_when_ranking_ignores_it():
    """The failure this exists for: 'compare @A and @B' where A takes every
    slot by distance, so B — explicitly named — contributes nothing."""
    ordered = _chunks("aaaaaaaaab")

    kept = apply_per_paper_floor(
        ordered, paper_of=lambda c: c[0], scope=["a", "b"], floor=2, budget=6
    )

    assert _papers(kept).count("b") >= 1
    assert len(kept) == 6


def test_the_floor_is_taken_round_robin_from_each_papers_own_best():
    ordered = _chunks("aabbcc")

    kept = apply_per_paper_floor(
        ordered, paper_of=lambda c: c[0], scope=["a", "b", "c"], floor=1, budget=6
    )

    assert _papers(kept)[:3] == "abc"


def test_the_budget_is_never_exceeded():
    ordered = _chunks("abcabcabc")

    kept = apply_per_paper_floor(
        ordered, paper_of=lambda c: c[0], scope=["a", "b", "c"], floor=2, budget=4
    )

    assert len(kept) == 4


def test_a_paper_with_fewer_chunks_than_the_floor_does_not_steal_slots():
    """b has one chunk. Its floor is one, not two, and the spare slot goes back
    to the distance fill rather than being held empty."""
    ordered = _chunks("aaaab")

    kept = apply_per_paper_floor(
        ordered, paper_of=lambda c: c[0], scope=["a", "b"], floor=2, budget=5
    )

    assert len(kept) == 5
    assert _papers(kept).count("b") == 1


def test_a_paper_in_scope_with_no_chunks_at_all_is_simply_absent():
    ordered = _chunks("aaa")

    kept = apply_per_paper_floor(
        ordered, paper_of=lambda c: c[0], scope=["a", "zz"], floor=2, budget=3
    )

    assert len(kept) == 3


def test_relative_order_within_a_paper_is_preserved():
    """Callers number citations over the FINAL order, so a reshuffle inside a
    paper would hand the model excerpts labelled out of relevance order."""
    ordered = _chunks("aaabbb")

    kept = apply_per_paper_floor(
        ordered, paper_of=lambda c: c[0], scope=["a", "b"], floor=2, budget=6
    )

    a_indices = [i for p, i in kept if p == "a"]
    assert a_indices == sorted(a_indices)


def test_no_floor_means_plain_truncation():
    ordered = _chunks("aaabbb")

    kept = apply_per_paper_floor(
        ordered, paper_of=lambda c: c[0], scope=["a", "b"], floor=0, budget=4
    )

    assert _papers(kept) == "aaab"
