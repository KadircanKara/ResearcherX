"""Refuse a run whose configured retrieval stages did not actually build.

`build_chat_reranker` / `build_fusion_scorer` return None for a missing key,
the same None as "stage off", so a run configured for an arm can silently
measure plain RRF under that arm's label. Pure: the caller passes what it
built.
"""

from __future__ import annotations

from typing import Any


def stage_config_errors(
    settings: Any, reranker: object | None, fusion_scorer: object | None
) -> list[str]:
    errors: list[str] = []
    if settings.fusion_mode == "jev" and fusion_scorer is None:
        errors.append(
            "FUSION_MODE=jev but the Jev fusion scorer did not build "
            "(DECISIONS_API_KEY is empty); the run would measure plain RRF"
        )
    if settings.rerank_enabled and reranker is None:
        key = "DECISIONS_API_KEY" if settings.rerank_provider == "jev" else "COHERE_API_KEY"
        errors.append(
            f"RERANK_ENABLED with RERANK_PROVIDER={settings.rerank_provider} but the "
            f"reranker did not build ({key} is empty); the run would measure an "
            "un-reranked order. Set the key, or run arm A explicitly with RERANK_ENABLED=false"
        )
    return errors
