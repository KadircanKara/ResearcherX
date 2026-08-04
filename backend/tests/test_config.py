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


def test_prod_accepts_hosted_embedding_base_url(monkeypatch):
    _set_valid_prod(monkeypatch)
    settings.validate_for_environment()  # must not raise
