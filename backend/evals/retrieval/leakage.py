"""Lexical-leakage check for golden-set questions (PURE: no DB, no ORM).

A question's wording must fit its kind. A `paraphrase` must not share rare
tokens with its evidence (or a lexical retriever would find it by accident); a
`keyword` question must; a `paraphrase_keyword` may share rare tokens only
through its declared `keyword`. Document frequencies are an input, so the
rules are testable without a database; the validator script supplies them.

Tokens come from `app.services.bm25_tokenizer.terms`, the tokenizer the bm25
index uses, so "rare" means rare in the stemmed vocabulary retrieval sees.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.services.bm25_tokenizer import terms
from evals.retrieval.golden_set import Case

RARE_DF_FRACTION = 0.01
KEYWORD_OVERLAP_MIN = 0.60
KEYWORD_MIN_RARE = 2


@dataclass(frozen=True)
class LeakageResult:
    ok: bool
    shared_rare: frozenset[str]
    reason: str


def rare_terms(question: str, evidence: str, df: Mapping[str, int], n_docs: int) -> frozenset[str]:
    """Question terms that also occur in `evidence` and are rare in the corpus.

    A term absent from `df` counts as document frequency 0, so it is rare.
    """
    limit = RARE_DF_FRACTION * n_docs
    shared = set(terms(question)) & set(terms(evidence))
    return frozenset(t for t in shared if df.get(t, 0) < limit)


def _fmt(tokens) -> str:
    return ", ".join(sorted(tokens))


def check(case: Case, evidence: str, df: Mapping[str, int], n_docs: int) -> LeakageResult:
    """Decide whether `case.question` is worded correctly for `case.kind`.

    `evidence` is the caller's union of the paper title and every satisfying
    chunk's section text and text.
    """
    if case.is_negative:
        return LeakageResult(True, frozenset(), "negative")

    shared = rare_terms(case.question, evidence, df, n_docs)

    if case.kind == "paraphrase":
        if shared:
            return LeakageResult(
                False, shared, f"shares rare tokens with the evidence: {_fmt(shared)}"
            )
        return LeakageResult(True, shared, "ok")

    if case.kind == "paraphrase_keyword":
        if not case.keyword:
            return LeakageResult(False, shared, "paraphrase_keyword has no keyword")
        if not shared:
            return LeakageResult(
                False,
                shared,
                "shares no rare token with the evidence, so the keyword leaks nothing",
            )
        extra = shared - set(terms(case.keyword))
        if extra:
            return LeakageResult(
                False, shared, f"shares rare tokens outside the keyword: {_fmt(extra)}"
            )
        return LeakageResult(True, shared, "ok")

    if case.kind == "keyword":
        question_terms = set(terms(case.question))
        if not question_terms:
            return LeakageResult(False, shared, "question has no content tokens")
        overlap = len(question_terms & set(terms(evidence))) / len(question_terms)
        if len(shared) < KEYWORD_MIN_RARE:
            return LeakageResult(
                False,
                shared,
                f"needs at least {KEYWORD_MIN_RARE} shared rare tokens, has {len(shared)}: {_fmt(shared)}",
            )
        if overlap < KEYWORD_OVERLAP_MIN:
            missing = question_terms - set(terms(evidence))
            return LeakageResult(
                False,
                shared,
                f"only {overlap:.0%} of question tokens appear in the evidence "
                f"(need {KEYWORD_OVERLAP_MIN:.0%}); missing: {_fmt(missing)}",
            )
        return LeakageResult(True, shared, "ok")

    return LeakageResult(False, shared, f"unknown kind {case.kind!r}")
