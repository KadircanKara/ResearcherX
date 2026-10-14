"""Which rerank and fusion stages the chat path builds, per settings (arms A–E2)."""

from app.local_rag.rerank import CohereReranker
from app.services import chat_service as cs
from app.services.jev_reranker import JevReranker


def _set(monkeypatch, **values):
    for k, v in values.items():
        monkeypatch.setattr(cs.settings, k, v)


def test_arm_b_default_is_cohere(monkeypatch):
    _set(
        monkeypatch,
        rerank_enabled=True,
        rerank_provider="cohere",
        cohere_api_key="c",
        fusion_mode="rrf",
    )
    assert isinstance(cs.build_chat_reranker(), CohereReranker)
    assert cs.build_fusion_scorer() is None


def test_arm_a_kill_switch(monkeypatch):
    _set(monkeypatch, rerank_enabled=False, cohere_api_key="c", fusion_mode="rrf")
    assert cs.build_chat_reranker() is None


def test_arm_c_jev_rerank(monkeypatch):
    _set(
        monkeypatch,
        rerank_enabled=True,
        rerank_provider="jev",
        decisions_api_key="k",
        fusion_mode="rrf",
    )
    assert isinstance(cs.build_chat_reranker(), JevReranker)


def test_arm_e_jev_fusion_without_rerank(monkeypatch):
    _set(monkeypatch, rerank_enabled=False, fusion_mode="jev", decisions_api_key="k")
    assert cs.build_chat_reranker() is None
    assert isinstance(cs.build_fusion_scorer(), JevReranker)


def test_arm_e2_jev_fusion_then_cohere(monkeypatch):
    _set(
        monkeypatch,
        rerank_enabled=True,
        rerank_provider="cohere",
        cohere_api_key="c",
        fusion_mode="jev",
        decisions_api_key="k",
    )
    assert isinstance(cs.build_chat_reranker(), CohereReranker)
    assert isinstance(cs.build_fusion_scorer(), JevReranker)


def test_jev_without_a_key_is_off(monkeypatch):
    _set(
        monkeypatch,
        rerank_enabled=True,
        rerank_provider="jev",
        decisions_api_key="",
        fusion_mode="jev",
    )
    assert cs.build_chat_reranker() is None
    assert cs.build_fusion_scorer() is None
