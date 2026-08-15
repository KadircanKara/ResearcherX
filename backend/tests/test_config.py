"""validate_for_environment(): prod refuses dev fallbacks (LLM key, sqlite,
owner key, embedding key, and — the case this file adds — a dev/local
embedding endpoint like Ollama or localhost).

The single-paper scope relationship checks (intra_paper_delta,
intra_paper_ceiling vs. similarity_threshold) are NOT part of
validate_for_environment() — they run unconditionally in a
`@model_validator(mode="after")` on `Settings` (see
`_check_intra_paper_relationship` in app/core/config.py), so they are
covered separately below by constructing `Settings` directly rather than by
calling validate_for_environment() on the module-level singleton.
"""

import pytest
from pydantic import ValidationError

from app.core.config import Settings, settings


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


def test_negative_intra_paper_delta_rejected_at_construction():
    """A negative delta makes keep_within_paper return 0, emptying every
    single-paper retrieval -- and a single-paper project has no untargeted
    fallback, so the model would answer ungrounded. This is NOT prod-only:
    the delta sweep in evals/retrieval/README.md sets INTRA_PAPER_DELTA as a
    dev env override, which never reaches validate_for_environment() -- so
    the guard must fire at construction, in every environment."""
    with pytest.raises(ValidationError, match="INTRA_PAPER_DELTA"):
        Settings(intra_paper_delta=-0.01)


def test_ceiling_below_similarity_threshold_rejected_at_construction():
    """A ceiling below the global threshold would silently make single-paper
    scope STRICTER than global scope -- the opposite of its purpose as a
    looser noise floor. Also environment-independent, so also checked at
    construction rather than behind the prod-only gate."""
    with pytest.raises(ValidationError, match="INTRA_PAPER_CEILING"):
        Settings(intra_paper_ceiling=0.5, similarity_threshold=0.75)


def test_settings_construct_normally_with_shipped_defaults():
    """The shipped defaults (delta 0.20, ceiling 0.85 against threshold 0.75)
    must pass `_check_intra_paper_relationship` -- this is exactly what
    backend/tests/conftest.py relies on when it imports app.main (and
    transitively app.core.config) at collection time. A regression here
    would fail every test in the suite, not just this one."""
    Settings()  # must not raise


def test_hybrid_weights_must_sum_to_one():
    """The weights are a 70/30 split of one unit of ranking authority. Two
    weights summing to 1.3 still 'work' arithmetically -- RRF is linear -- so
    nothing would fail, the fused scores would just be uniformly inflated and
    the ratio would silently be something other than what was configured."""
    with pytest.raises(ValidationError):
        Settings(hybrid_dense_weight=0.7, hybrid_sparse_weight=0.5)


def test_hybrid_weights_may_not_be_negative():
    with pytest.raises(ValidationError):
        Settings(hybrid_dense_weight=1.3, hybrid_sparse_weight=-0.3)


def test_hybrid_rrf_k_must_be_positive():
    """k=0 makes the rank-1 term 1/1 and rank-2 term 1/2 -- a fusion so steep
    that the top chunk of either arm dominates everything. Negative k divides
    by zero at rank |k|."""
    with pytest.raises(ValidationError):
        Settings(hybrid_rrf_k=0)


def test_hybrid_pools_must_be_positive():
    with pytest.raises(ValidationError):
        Settings(hybrid_sparse_pool=0)


def test_intra_paper_rank_window_must_be_positive():
    """Window 0 empties every single-paper retrieval, and a single-paper
    project has no untargeted scope to fall back to -- the model would answer
    ungrounded. Same failure mode as a negative intra_paper_delta."""
    with pytest.raises(ValidationError):
        Settings(intra_paper_rank_window=0)


def test_shipped_hybrid_defaults_are_valid():
    """The defaults must never block a normal dev or test boot."""
    settings = Settings()
    assert settings.hybrid_retrieval is True
    assert settings.hybrid_dense_weight == 0.7
    assert settings.hybrid_sparse_weight == 0.3
    assert settings.hybrid_rrf_k == 30
