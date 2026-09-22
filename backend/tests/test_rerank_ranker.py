"""apply_rerank: the entire rerank ORDERING policy.

Pure on purpose, exactly like hybrid_ranker and mention_ranker: production
(chat_service) and both eval harnesses call this same function, so the
policy that ships cannot drift from the policy that is measured. The
network call lives in app/local_rag/rerank.py and is not exercised here.
"""

from app.local_rag.rerank import RerankResult
from app.services.rerank_ranker import apply_rerank, rerank_items


def _r(index: int, score: float) -> RerankResult:
    return RerankResult(index=index, relevance_score=score)


def test_no_opinion_leaves_the_order_untouched():
    """The fail-open contract. `None` is every failure the client has --
    an outage, a bad payload, a missing key -- and it must degrade the
    turn's ORDERING and nothing else."""
    items = ["a", "b", "c"]
    assert apply_rerank(items, None) == ["a", "b", "c"]


def test_empty_results_leave_the_order_untouched():
    """Distinct from None: the client answers `[]` for an empty document
    list. Same outcome, and it must not be mistaken for 'drop everything'."""
    assert apply_rerank(["a", "b"], []) == ["a", "b"]


def test_items_are_reordered_by_relevance_score():
    items = ["a", "b", "c"]
    assert apply_rerank(items, [_r(2, 0.9), _r(0, 0.5), _r(1, 0.1)]) == ["c", "a", "b"]


def test_results_are_sorted_here_and_not_trusted_to_arrive_sorted():
    """The client sorts, but this function is what production's excerpt
    order actually is. A provider returning results in request order would
    otherwise silently reorder the model's catalog."""
    items = ["a", "b", "c"]
    assert apply_rerank(items, [_r(0, 0.1), _r(1, 0.9), _r(2, 0.5)]) == ["b", "c", "a"]


def test_unscored_items_are_kept_at_the_tail_in_their_original_order():
    """top_n is smaller than the candidate list, so most candidates come
    back unscored. Dropping them would shrink the pool below the context
    budget -- the rerank decides ORDER, never admission."""
    items = ["a", "b", "c", "d", "e"]
    assert apply_rerank(items, [_r(3, 0.9), _r(1, 0.4)]) == ["d", "b", "a", "c", "e"]


def test_nothing_is_dropped_and_nothing_is_duplicated():
    items = [f"chunk-{i}" for i in range(10)]
    out = apply_rerank(items, [_r(7, 0.9), _r(0, 0.8)])
    assert sorted(out) == sorted(items)
    assert len(out) == len(items)


def test_a_repeated_index_is_honoured_once():
    """Defensive: a duplicated index would otherwise duplicate a chunk in
    the model's catalog, and two identical excerpts under different
    citation numbers is a citation bug, not a ranking one."""
    items = ["a", "b", "c"]
    assert apply_rerank(items, [_r(2, 0.9), _r(2, 0.8), _r(0, 0.7)]) == ["c", "a", "b"]


def test_an_out_of_range_index_is_ignored_rather_than_raising():
    """The client already filters these. This is the second guard: an index
    that cannot be mapped would address the wrong candidate, which reads as
    a plausible answer citing the wrong paper."""
    items = ["a", "b"]
    assert apply_rerank(items, [_r(5, 0.9), _r(1, 0.8)]) == ["b", "a"]
    assert apply_rerank(items, [_r(-1, 0.9)]) == ["a", "b"]


def test_an_empty_candidate_list_stays_empty():
    assert apply_rerank([], [_r(0, 0.9)]) == []


def test_the_returned_list_is_a_new_object():
    """Callers slice the result to the budget; mutating the input list
    would corrupt a candidate list the caller still holds."""
    items = ["a", "b"]
    out = apply_rerank(items, None)
    assert out is not items


class _FakeReranker:
    """Records what it was asked, answers what it was told to."""

    def __init__(self, results=None):
        self._results = results
        self.calls: list[dict] = []

    async def rerank(self, query: str, documents: list[str], top_n: int):
        self.calls.append({"query": query, "documents": documents, "top_n": top_n})
        return self._results


async def test_no_reranker_returns_the_incoming_order_without_calling_anything():
    """The kill switch off, or no API key. One degraded path, not two."""
    assert await rerank_items(["a", "b"], query="q", text_of=str, reranker=None, limit=10) == [
        "a",
        "b",
    ]


async def test_a_failing_reranker_leaves_the_incoming_order():
    fake = _FakeReranker(results=None)
    out = await rerank_items(["a", "b", "c"], query="q", text_of=str, reranker=fake, limit=10)
    assert out == ["a", "b", "c"]
    assert len(fake.calls) == 1


async def test_only_the_first_limit_items_are_sent_and_the_tail_keeps_its_order():
    """The bound on what crosses the wire. Everything past `limit` still
    reaches the caller — this stage orders, it never filters."""
    items = [f"c{i}" for i in range(8)]
    fake = _FakeReranker(results=[_r(2, 0.9), _r(0, 0.5)])
    out = await rerank_items(items, query="q", text_of=str, reranker=fake, limit=3)
    assert fake.calls[0]["documents"] == ["c0", "c1", "c2"]
    assert out == ["c2", "c0", "c1", "c3", "c4", "c5", "c6", "c7"]


async def test_every_document_sent_is_scored():
    """top_n equals the number sent: the budget cut downstream selects, so
    asking for a shorter list here would silently make this a filter."""
    fake = _FakeReranker(results=[])
    await rerank_items(["a", "b", "c"], query="q", text_of=str, reranker=fake, limit=2)
    assert fake.calls[0]["top_n"] == 2


async def test_text_of_is_what_reaches_the_provider():
    """Callers hold rows, not strings; the query text and the document text
    must both be the model-visible text, not a repr."""
    fake = _FakeReranker(results=[])
    await rerank_items(
        [{"text": "alpha"}, {"text": "beta"}],
        query="the question",
        text_of=lambda row: row["text"],
        reranker=fake,
        limit=10,
    )
    assert fake.calls[0]["documents"] == ["alpha", "beta"]
    assert fake.calls[0]["query"] == "the question"


async def test_an_empty_candidate_list_never_calls_the_provider():
    fake = _FakeReranker(results=[])
    assert await rerank_items([], query="q", text_of=str, reranker=fake, limit=10) == []
    assert fake.calls == []
