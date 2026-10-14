"""JevReranker: one OpenRouter /systemone request per document, fail-open as a whole.

Network-free: every test injects an httpx.MockTransport client."""

import json
import math

import httpx
import pytest

from app.services import jev_reranker as jr
from app.services.jev_reranker import JevReranker


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _reranker(handler, **kw) -> JevReranker:
    args = dict(
        api_key="k",
        model="~typesafe/jev-latest",
        base_url="https://openrouter.test/api/v1/systemone",
        question="noul",
        concurrency=4,
        timeout_s=1.0,
        client=_client(handler),
    )
    args.update(kw)
    return JevReranker(**args)


def _noul(p: float, cost: float = 0.00001) -> dict:
    return {
        "model": "typesafe/jev-1.13-20260917",
        "answers": {"relevant": {"type": "noul", "noul": p}},
        "usage": {"input_tokens": 300, "output_tokens": 20, "cost": cost},
    }


def test_payload_puts_question_and_passage_in_state_and_one_noul():
    p = jr.build_payload("m", "noul", "how do swarms stay connected?", "relay chain")
    assert p["model"] == "m"
    assert p["state"] == {"question": "how do swarms stay connected?", "passage": "relay chain"}
    q = p["questions"]["relevant"]
    assert q["type"] == "noul"
    assert "`passage`" in q["instructions"] and "`question`" in q["instructions"]
    assert set(q["criteria"]) == {"true", "false"}


def test_score_payload_has_four_ordered_levels():
    q = jr.build_payload("m", "score", "q", "d")["questions"]["relevant"]
    assert q["type"] == "score"
    assert len(q["criteria"]) == 4


def test_parse_noul_and_score():
    assert jr.parse_relevance(_noul(0.8), "noul") == 0.8
    body = {"answers": {"relevant": {"type": "score", "score": 1.5}}}
    assert jr.parse_relevance(body, "score") == pytest.approx(0.5)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"answers": {}},
        {"answers": {"relevant": {"type": "score", "score": 2}}},  # asked noul
        {"answers": {"relevant": {"type": "noul", "noul": "high"}}},
        {"answers": {"relevant": {"type": "noul", "noul": math.nan}}},
        {"answers": {"relevant": {"type": "noul", "noul": 1.4}}},
    ],
)
def test_parse_rejects_malformed_bodies(body):
    with pytest.raises(ValueError):
        jr.parse_relevance(body, "noul")


async def test_scores_every_document_and_sorts_best_first():
    scores = {"a": 0.2, "b": 0.9, "c": 0.5}
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        seen.append((request.headers["authorization"], payload["state"]["passage"]))
        return httpx.Response(200, json=_noul(scores[payload["state"]["passage"]]))

    out = await _reranker(handler).rerank("q", ["a", "b", "c"], top_n=3)
    assert [(r.index, r.relevance_score) for r in out] == [(1, 0.9), (2, 0.5), (0, 0.2)]
    assert sorted(p for _, p in seen) == ["a", "b", "c"]
    assert all(auth == "Bearer k" for auth, _ in seen)


async def test_top_n_truncates_after_sorting():
    def handler(request):
        return httpx.Response(200, json=_noul(0.1 if b"x" in request.content else 0.7))

    out = await _reranker(handler).rerank("q", ["x", "y"], top_n=1)
    assert [r.index for r in out] == [1]


async def test_documents_are_capped_like_cohere():
    lengths = []

    def handler(request):
        lengths.append(len(json.loads(request.content)["state"]["passage"]))
        return httpx.Response(200, json=_noul(0.5))

    await _reranker(handler).rerank("q", ["z" * 5000], top_n=1)
    assert lengths == [2000]


async def test_empty_documents_make_no_call():
    def handler(request):
        raise AssertionError("no request expected")

    assert await _reranker(handler).rerank("q", [], top_n=5) == []


async def test_no_key_is_no_opinion():
    def handler(request):
        raise AssertionError("no request expected")

    assert await _reranker(handler, api_key="").rerank("q", ["a"], top_n=1) is None


async def test_a_429_is_retried(monkeypatch):
    monkeypatch.setattr(jr, "_BACKOFF_S", 0.0)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] <= 2:
            return httpx.Response(429)
        return httpx.Response(200, json=_noul(0.6))

    out = await _reranker(handler).rerank("q", ["a"], top_n=1)
    assert out is not None and out[0].relevance_score == 0.6
    assert calls["n"] == 3


async def test_one_exhausted_request_fails_the_whole_call_open(monkeypatch):
    """Partial scores would put the failed chunk at the tail: never used."""
    monkeypatch.setattr(jr, "_BACKOFF_S", 0.0)

    def handler(request):
        if b'"passage": "bad"' in request.content or b'"passage":"bad"' in request.content:
            return httpx.Response(429)
        return httpx.Response(200, json=_noul(0.5))

    assert await _reranker(handler).rerank("q", ["ok", "bad", "ok2"], top_n=3) is None


async def test_a_malformed_200_fails_open():
    def handler(request):
        return httpx.Response(200, json={"answers": {"relevant": {"type": "score", "score": 1}}})

    assert await _reranker(handler).rerank("q", ["a"], top_n=1) is None


async def test_a_400_is_not_retried():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(400, json={"error": "bad"})

    assert await _reranker(handler).rerank("q", ["a"], top_n=1) is None
    assert calls["n"] == 1


async def test_concurrency_is_bounded():
    import asyncio

    state = {"now": 0, "peak": 0}

    async def handler(request):
        state["now"] += 1
        state["peak"] = max(state["peak"], state["now"])
        await asyncio.sleep(0.01)
        state["now"] -= 1
        return httpx.Response(200, json=_noul(0.5))

    await _reranker(handler, concurrency=3).rerank("q", [str(i) for i in range(12)], top_n=12)
    assert state["peak"] <= 3


def test_build_returns_none_without_key(monkeypatch):
    monkeypatch.setattr(jr.settings, "decisions_api_key", "")
    assert jr.build_jev_reranker() is None


def test_build_uses_settings(monkeypatch):
    monkeypatch.setattr(jr.settings, "decisions_api_key", "k")
    monkeypatch.setattr(jr.settings, "jev_question", "score")
    r = jr.build_jev_reranker()
    assert isinstance(r, JevReranker) and r.question == "score"
