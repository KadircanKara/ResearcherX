"""EmbeddingService unit tests — HTTP call mocked."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.embedding_service import EmbeddingService


def _make_embedding_response(n_inputs: int, dim: int = 768):
    """Fake OpenAI embeddings.create() response."""
    data = [MagicMock(index=i, embedding=[0.1] * dim) for i in range(n_inputs)]
    return MagicMock(data=data)


async def test_embed_single():
    svc = EmbeddingService()
    mock_response = _make_embedding_response(1)
    with patch.object(
        svc._client, "embeddings", new=MagicMock(create=AsyncMock(return_value=mock_response))
    ):
        result = await svc.embed("hello world", task_type="RETRIEVAL_QUERY")
    assert len(result) == 768
    assert result[0] == pytest.approx(0.1)


async def test_embed_batch():
    svc = EmbeddingService()
    mock_response = _make_embedding_response(3)
    with patch.object(
        svc._client, "embeddings", new=MagicMock(create=AsyncMock(return_value=mock_response))
    ):
        results = await svc.embed_batch(["a", "b", "c"], task_type="RETRIEVAL_DOCUMENT")
    assert len(results) == 3
    assert all(len(r) == 768 for r in results)


async def test_embed_batch_empty():
    results = await EmbeddingService().embed_batch([], task_type="RETRIEVAL_DOCUMENT")
    assert results == []


from types import SimpleNamespace

from app.core.config import settings


class _CaptureEmbeddings:
    """Stands in for client.embeddings — records the kwargs it was called with."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        n = len(kwargs["input"])
        return SimpleNamespace(
            data=[SimpleNamespace(index=i, embedding=[0.0] * 768) for i in range(n)]
        )


class _CaptureClient:
    def __init__(self) -> None:
        self.embeddings = _CaptureEmbeddings()


async def test_document_prefix_applied_to_chunks():
    svc = EmbeddingService()
    client = _CaptureClient()
    svc._client = client
    with (
        patch.object(settings, "embedding_document_prefix", "search_document:"),
        patch.object(settings, "embedding_query_prefix", "search_query:"),
    ):
        await svc.embed_batch(["hello world"], task_type="RETRIEVAL_DOCUMENT")
    assert client.embeddings.calls[0]["input"] == ["search_document: hello world"]


async def test_query_prefix_applied_to_queries():
    svc = EmbeddingService()
    client = _CaptureClient()
    svc._client = client
    with (
        patch.object(settings, "embedding_document_prefix", "search_document:"),
        patch.object(settings, "embedding_query_prefix", "search_query:"),
    ):
        await svc.embed("what method is proposed", task_type="RETRIEVAL_QUERY")
    assert client.embeddings.calls[0]["input"] == ["search_query: what method is proposed"]


async def test_empty_prefix_leaves_text_untouched():
    """Prod/OpenAI path: defaults are empty, so nothing is prepended."""
    svc = EmbeddingService()
    client = _CaptureClient()
    svc._client = client
    with (
        patch.object(settings, "embedding_document_prefix", ""),
        patch.object(settings, "embedding_query_prefix", ""),
    ):
        await svc.embed_batch(["untouched"], task_type="RETRIEVAL_DOCUMENT")
    assert client.embeddings.calls[0]["input"] == ["untouched"]
