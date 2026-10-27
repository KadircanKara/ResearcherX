"""The leakage checker is pure: DF is an input, so no database is needed."""

from __future__ import annotations

from app.services.bm25_tokenizer import terms
from evals.retrieval.golden_set import Case
from evals.retrieval.leakage import check, rare_terms

N_DOCS = 1000  # rare means df < 10


def _df(rare: list[str], common: list[str]) -> dict[str, int]:
    out = {t: 1 for w in rare for t in terms(w)}
    out.update({t: 500 for w in common for t in terms(w)})
    return out


def _case(kind: str, question: str, keyword: str | None = None) -> Case:
    return Case(
        id="c",
        kind=kind,
        question=question,
        paper_title_contains=None if kind == "off_topic" else "x",
        expect_substrings=() if kind == "off_topic" else ("y",),
        keyword=keyword,
    )


DF = _df(
    rare=["maddpg", "consensus", "convergence", "auction", "bound"],
    common=["drone", "accuracy", "agents", "search", "method"],
)


def test_rare_terms_treats_a_missing_df_as_rare():
    assert rare_terms("zyxwv drone", "zyxwv drone", DF, N_DOCS) == frozenset(terms("zyxwv"))


def test_paraphrase_passes_with_only_common_overlap():
    r = check(
        _case("paraphrase", "How accurate is the drone search method?"),
        "The drone search method reaches high accuracy with maddpg agents.",
        DF,
        N_DOCS,
    )
    assert r.ok and r.shared_rare == frozenset()


def test_paraphrase_fails_on_a_shared_rare_token():
    r = check(
        _case("paraphrase", "Does consensus help the drone agents?"),
        "Agents reach consensus on the drone plan.",
        DF,
        N_DOCS,
    )
    assert not r.ok
    assert terms("consensus")[0] in r.reason


def test_paraphrase_fails_when_rare_token_is_only_in_title():
    evidence = "Auction Methods for Drones\nThe drone search method is accurate."
    r = check(_case("paraphrase", "Is an auction used for drone search?"), evidence, DF, N_DOCS)
    assert not r.ok
    assert terms("auction")[0] in r.reason


def test_paraphrase_keyword_passes_when_rare_overlap_is_within_keyword():
    r = check(
        _case("paraphrase_keyword", "How do drone agents learn with MADDPG?", "MADDPG"),
        "We train drone agents using maddpg.",
        DF,
        N_DOCS,
    )
    assert r.ok
    assert r.shared_rare == frozenset(terms("maddpg"))


def test_paraphrase_keyword_fails_on_extra_rare_token_outside_keyword():
    r = check(
        _case(
            "paraphrase_keyword",
            "Does MADDPG reach consensus among drone agents?",
            "MADDPG",
        ),
        "maddpg drone agents reach consensus.",
        DF,
        N_DOCS,
    )
    assert not r.ok
    assert terms("consensus")[0] in r.reason


def test_paraphrase_keyword_fails_with_no_rare_overlap():
    r = check(
        _case("paraphrase_keyword", "How do drone agents learn?", "MADDPG"),
        "We train drone agents using maddpg.",
        DF,
        N_DOCS,
    )
    assert not r.ok


def test_keyword_needs_two_rare_tokens():
    r = check(
        _case("keyword", "consensus drone"),
        "consensus among drone agents",
        DF,
        N_DOCS,
    )
    assert not r.ok
    r = check(
        _case("keyword", "consensus convergence"),
        "consensus convergence of drone agents",
        DF,
        N_DOCS,
    )
    assert r.ok


def test_keyword_needs_60_percent_overlap():
    q = _case("keyword", "consensus convergence bound zzzone zzztwo")
    df = {**DF}
    three = check(q, "consensus convergence bound", df, N_DOCS)
    assert three.ok
    two = check(q, "consensus convergence", df, N_DOCS)
    assert not two.ok


def test_keyword_with_only_stopwords_fails_cleanly():
    r = check(_case("keyword", "the of and"), "the of and consensus", DF, N_DOCS)
    assert not r.ok
    assert r.reason


def test_off_topic_is_not_checked():
    r = check(_case("off_topic", "What is the capital of France?"), "", DF, N_DOCS)
    assert r.ok and r.reason == "negative" and r.shared_rare == frozenset()
