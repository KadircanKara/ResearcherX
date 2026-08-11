"""validate_for_environment(): prod refuses dev fallbacks (LLM key, sqlite,
owner key, embedding key, and — the case this file adds — a dev/local
embedding endpoint like Ollama or localhost).
"""

import pytest

from app.core.config import settings


def _set_valid_prod(monkeypatch):
    """A prod config that should pass every existing + new guard clause."""
    monkeypatch.setattr(settings, "environment", "prod")
    monkeypatch.setattr(settings, "llm_api_key", "llm-key")
    monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://u:p@db:5432/researcherx")
    monkeypatch.setattr(settings, "owner_api_key", "owner-key")
    monkeypatch.setattr(settings, "embedding_api_key", "embedding-key")
    monkeypatch.setattr(settings, "embedding_base_url", "https://api.openai.com/v1")


def test_prod_rejects_ollama_embedding_base_url(monkeypatch):
    _set_valid_prod(monkeypatch)
    monkeypatch.setattr(settings, "embedding_base_url", "http://ollama:11434/v1")
    with pytest.raises(RuntimeError, match="EMBEDDING_BASE_URL"):
        settings.validate_for_environment()


def test_prod_rejects_localhost_embedding_base_url(monkeypatch):
    _set_valid_prod(monkeypatch)
    monkeypatch.setattr(settings, "embedding_base_url", "http://localhost:11434/v1")
    with pytest.raises(RuntimeError, match="EMBEDDING_BASE_URL"):
        settings.validate_for_environment()


def test_prod_rejects_dev_host_regardless_of_case(monkeypatch):
    """Guard against a false negative: the match must not be case-sensitive.

    A false positive here merely refuses to boot with a clear message; a
    false negative reproduces the original silent-failure-at-runtime bug
    this guard exists to prevent.
    """
    _set_valid_prod(monkeypatch)
    monkeypatch.setattr(settings, "embedding_base_url", "http://OLLAMA:11434/v1")
    with pytest.raises(RuntimeError, match="EMBEDDING_BASE_URL"):
        settings.validate_for_environment()


def test_prod_accepts_hosted_embedding_base_url(monkeypatch):
    _set_valid_prod(monkeypatch)
    settings.validate_for_environment()  # must not raise


def test_prod_rejects_negative_intra_paper_delta(monkeypatch):
    """A negative delta makes keep_within_paper return 0, emptying every
    single-paper retrieval -- and a single-paper project has no untargeted
    fallback, so the model would answer ungrounded."""
    _set_valid_prod(monkeypatch)
    monkeypatch.setattr(settings, "intra_paper_delta", -0.01)
    with pytest.raises(RuntimeError, match="INTRA_PAPER_DELTA"):
        settings.validate_for_environment()


def test_prod_rejects_ceiling_below_similarity_threshold(monkeypatch):
    """A ceiling below the global threshold would silently make single-paper
    scope STRICTER than global scope -- the opposite of its purpose as a
    looser noise floor."""
    _set_valid_prod(monkeypatch)
    monkeypatch.setattr(settings, "similarity_threshold", 0.90)
    monkeypatch.setattr(settings, "intra_paper_ceiling", 0.85)
    with pytest.raises(RuntimeError, match="INTRA_PAPER_CEILING"):
        settings.validate_for_environment()
