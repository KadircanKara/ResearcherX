"""Arm table, parsing and the counting/memo wrapper for the retrieval harness."""

import pytest

from app.local_rag.rerank import RerankResult
from evals.retrieval.arms import ARMS, ArmReranker, needs, parse_arms


def test_six_arms_match_the_spec():
    assert {k: (v.fusion, v.rerank) for k, v in ARMS.items()} == {
        "A": ("rrf", "none"),
        "B": ("rrf", "cohere"),
        "C": ("rrf", "jev-noul"),
        "D": ("rrf", "jev-score"),
        "E": ("jev", "none"),
        "E2": ("jev", "cohere"),
    }


def test_parse_arms_keeps_order_and_dedupes():
    assert parse_arms("a, B,E2,b") == ["A", "B", "E2"]


@pytest.mark.parametrize("bad", ["", "Z", "A,,X"])
def test_parse_arms_rejects_unknown(bad):
    with pytest.raises(ValueError):
        parse_arms(bad)


def test_needs_lists_backends():
    assert needs(["A"]) == set()
    assert needs(["B", "E"]) == {"cohere", "jev-noul"}
    assert needs(["D"]) == {"jev-score"}


class _Inner:
    def __init__(self, result):
        self.result, self.calls = result, 0

    async def rerank(self, query, documents, top_n):
        self.calls += 1
        return self.result


async def test_arm_reranker_memoises_identical_requests():
    inner = _Inner([RerankResult(0, 0.5)])
    w = ArmReranker(inner)
    await w.rerank("q", ["a"], 1)
    await w.rerank("q", ["a"], 1)
    assert inner.calls == 1 and w.calls == 2 and w.no_opinion == 0


async def test_arm_reranker_measures_score_resolution():
    """Jev answers to 2 decimals, so many chunks tie; ties keep the incoming (RRF)
    order. The distinct fraction says how much of an arm's order Jev decided."""
    w = ArmReranker(
        _Inner(
            [
                RerankResult(0, 0.98),
                RerankResult(1, 0.98),
                RerankResult(2, 0.5),
                RerankResult(3, 0.1),
            ]
        )
    )
    await w.rerank("q", ["a", "b", "c", "d"], 4)
    assert w.distinct_fraction() == 0.75


async def test_arm_reranker_counts_no_opinion():
    """A Jev arm whose every call failed open must read as a no-op, not as a result."""
    w = ArmReranker(_Inner(None))
    await w.rerank("q", ["a"], 1)
    await w.rerank("q", ["b"], 1)
    assert w.no_opinion == 2 and w.calls == 2
