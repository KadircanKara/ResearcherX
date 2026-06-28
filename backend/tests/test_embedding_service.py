"""EmbeddingService unit tests — HTTP call mocked."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.embedding_service import EmbeddingService


def _make_embedding_response(n_inputs: int, dim: int = 768):
    """Fake OpenAI embeddings.create() response."""
    data = [
        MagicMock(index=i, embedding=[0.1] * dim)
        for i in range(n_inputs)
    ]
    return MagicMock(data=data)


async def test_embed_single():
    svc = EmbeddingService()
    mock_response = _make_embedding_response(1)
    with patch.object(svc._client, "embeddings", new=MagicMock(
        create=AsyncMock(return_value=mock_response)
    )):
        result = await svc.embed("hello world", task_type="RETRIEVAL_QUERY")
    assert len(result) == 768
    assert result[0] == pytest.approx(0.1)


async def test_embed_batch():
    svc = EmbeddingService()
    mock_response = _make_embedding_response(3)
    with patch.object(svc._client, "embeddings", new=MagicMock(
        create=AsyncMock(return_value=mock_response)
    )):
        results = await svc.embed_batch(["a", "b", "c"], task_type="RETRIEVAL_DOCUMENT")
    assert len(results) == 3
    assert all(len(r) == 768 for r in results)


async def test_embed_batch_empty():
    results = await EmbeddingService().embed_batch([], task_type="RETRIEVAL_DOCUMENT")
    assert results == []
