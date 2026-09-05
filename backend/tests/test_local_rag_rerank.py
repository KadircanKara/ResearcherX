"""app/local_rag/rerank.py — the Cohere /v2/rerank client.

Fail-open is the load-bearing behaviour: a reranker outage must degrade a
turn to the fused RRF order, never fail it. So `rerank` returns None on
every failure path rather than raising, and the caller keeps its own order.
"""

import httpx
import pytest

from app.local_rag.rerank import CohereReranker

_DOCS = ["swarm coverage path planning", "battery chemistry", "revisit time minimisation"]


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_sends_model_query_and_documents_as_strings():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.update(json.loads(request.content))
        seen["auth"] = request.headers.get("authorization")
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"results": []})

    reranker = CohereReranker(api_key="key-123", client=_client(handler))
    await reranker.rerank("coverage planning", _DOCS, top_n=2)

    assert seen["url"] == "https://api.cohere.com/v2/rerank"
    assert seen["auth"] == "Bearer key-123"
    assert seen["model"] == "rerank-v3.5"
    assert seen["query"] == "coverage planning"
    assert seen["documents"] == _DOCS  # v2 takes plain strings, not objects
    assert seen["top_n"] == 2


async def test_returns_indices_into_the_documents_passed_in_ordered_by_score():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 2, "relevance_score": 0.91},
                    {"index": 0, "relevance_score": 0.42},
                ]
            },
        )

    reranker = CohereReranker(api_key="k", client=_client(handler))
    results = await reranker.rerank("q", _DOCS, top_n=2)

    assert [r.index for r in results] == [2, 0]
    assert results[0].relevance_score == pytest.approx(0.91)


async def test_results_are_sorted_even_when_the_provider_does_not_sort_them():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 0, "relevance_score": 0.10},
                    {"index": 1, "relevance_score": 0.80},
                ]
            },
        )

    reranker = CohereReranker(api_key="k", client=_client(handler))
    results = await reranker.rerank("q", _DOCS, top_n=2)

    assert [r.index for r in results] == [1, 0]


async def test_documents_are_truncated_to_max_doc_chars():
    """A long chunk is billed as several documents once it passes Cohere's
    per-document token line, and the tail of a chunk rarely decides
    relevance."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"results": []})

    reranker = CohereReranker(api_key="k", client=_client(handler), max_doc_chars=10)
    await reranker.rerank("q", ["x" * 500], top_n=1)

    assert seen["documents"] == ["x" * 10]


async def test_an_http_error_fails_open_to_none():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "upstream boom"})

    reranker = CohereReranker(api_key="k", client=_client(handler))
    assert await reranker.rerank("q", _DOCS, top_n=2) is None


async def test_a_timeout_fails_open_to_none():
    def handler(request: httpx.Request):
        raise httpx.ReadTimeout("too slow", request=request)

    reranker = CohereReranker(api_key="k", client=_client(handler))
    assert await reranker.rerank("q", _DOCS, top_n=2) is None


async def test_a_malformed_payload_fails_open_rather_than_raising():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    reranker = CohereReranker(api_key="k", client=_client(handler))
    assert await reranker.rerank("q", _DOCS, top_n=2) is None


async def test_an_out_of_range_index_from_the_provider_is_dropped():
    """Defensive: an index the caller cannot map is worse than a shorter
    list, because the caller indexes its candidate list with it."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 99, "relevance_score": 0.99},
                    {"index": 1, "relevance_score": 0.50},
                ]
            },
        )

    reranker = CohereReranker(api_key="k", client=_client(handler))
    results = await reranker.rerank("q", _DOCS, top_n=2)

    assert [r.index for r in results] == [1]


async def test_no_documents_short_circuits_without_a_request():
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"results": []})

    reranker = CohereReranker(api_key="k", client=_client(handler))
    assert await reranker.rerank("q", [], top_n=5) == []
    assert called is False


async def test_a_missing_api_key_fails_open_without_a_request():
    """Running with no key is a normal local state, not an error: the
    pipeline degrades to fused RRF order."""
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"results": []})

    reranker = CohereReranker(api_key="", client=_client(handler))
    assert await reranker.rerank("q", _DOCS, top_n=2) is None
    assert called is False
