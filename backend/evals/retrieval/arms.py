"""The six ranking arms of the Jev experiment, as data.

Spec: docs/superpowers/specs/2026-10-14-jev-reranking-design.md (local).
Each arm is a (fusion, rerank) pair run through the SAME `rank_candidates`
production calls; this module only names them and wraps the backends.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.local_rag.rerank import RerankResult
from app.services.rerank_ranker import Reranker

Backend = Literal["cohere", "jev-noul", "jev-score"]


@dataclass(frozen=True)
class ArmSpec:
    fusion: Literal["rrf", "jev"]
    rerank: Literal["none", "cohere", "jev-noul", "jev-score"]


ARMS: dict[str, ArmSpec] = {
    "A": ArmSpec("rrf", "none"),
    "B": ArmSpec("rrf", "cohere"),
    "C": ArmSpec("rrf", "jev-noul"),
    "D": ArmSpec("rrf", "jev-score"),
    "E": ArmSpec("jev", "none"),
    "E2": ArmSpec("jev", "cohere"),
}


def parse_arms(spec: str) -> list[str]:
    names = [p.strip().upper() for p in spec.split(",")]
    if not spec.strip() or any(not n or n not in ARMS for n in names):
        raise ValueError(f"--arms takes a comma list of {', '.join(ARMS)}; got {spec!r}")
    return list(dict.fromkeys(names))


def needs(arms: list[str]) -> set[str]:
    out: set[str] = set()
    for name in arms:
        arm = ARMS[name]
        if arm.fusion == "jev":
            out.add("jev-noul")
        if arm.rerank != "none":
            out.add(arm.rerank)
    return out


@dataclass
class ArmReranker:
    """Memoises identical requests (E and E2 share Jev's fusion scores) and
    counts calls that came back with no opinion, so an arm that silently
    failed open is reported as a no-op rather than as a measurement."""

    inner: Reranker
    calls: int = 0
    no_opinion: int = 0
    _memo: dict = field(default_factory=dict)
    _distinct: list[float] = field(default_factory=list)

    def distinct_fraction(self) -> float | None:
        """Mean share of distinct scores per answered call (1.0 = no ties)."""
        return sum(self._distinct) / len(self._distinct) if self._distinct else None

    async def rerank(
        self, query: str, documents: list[str], top_n: int
    ) -> list[RerankResult] | None:
        self.calls += 1
        key = (query, tuple(documents), top_n)
        if key not in self._memo:
            self._memo[key] = await self.inner.rerank(query, documents, top_n)
        result = self._memo[key]
        if result is None:
            self.no_opinion += 1
        elif result:
            self._distinct.append(len({r.relevance_score for r in result}) / len(result))
        return result
