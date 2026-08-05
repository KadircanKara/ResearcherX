"""Tests for the eval harness's golden-set loading and satisfaction predicate.

These are pure — no database, no embeddings — so they run under normal pytest.
"""

import json
from pathlib import Path

import pytest

from retrieval_eval.golden_set import Case, GoldenSetError, chunk_satisfies, load_golden_set


def _write(tmp_path: Path, payload: dict) -> Path:
    p = tmp_path / "gs.json"
    p.write_text(json.dumps(payload))
    return p


def test_loads_a_content_case(tmp_path: Path):
    path = _write(
        tmp_path,
        {
            "version": 1,
            "cases": [
                {
                    "id": "c1",
                    "kind": "content",
                    "question": "What about revisit time?",
                    "paper_title_contains": "Joint Optimization",
                    "expect_substrings": ["revisit time"],
                }
            ],
        },
    )
    cases = load_golden_set(path)
    assert len(cases) == 1
    assert cases[0].id == "c1"
    assert cases[0].expect_substrings == ("revisit time",)
    assert cases[0].is_negative is False


def test_loads_an_off_topic_case(tmp_path: Path):
    path = _write(
        tmp_path,
        {
            "version": 1,
            "cases": [{"id": "n1", "kind": "off_topic", "question": "Recipe for cake?"}],
        },
    )
    cases = load_golden_set(path)
    assert cases[0].is_negative is True
    assert cases[0].paper_title_contains is None
    assert cases[0].expect_substrings == ()


def test_rejects_off_topic_case_carrying_expectations(tmp_path: Path):
    """An off_topic case with expectations is a contradiction — fail loudly
    rather than silently scoring it as a positive."""
    path = _write(
        tmp_path,
        {
            "version": 1,
            "cases": [
                {
                    "id": "bad",
                    "kind": "off_topic",
                    "question": "Recipe?",
                    "expect_substrings": ["cake"],
                }
            ],
        },
    )
    with pytest.raises(GoldenSetError, match="off_topic"):
        load_golden_set(path)


def test_rejects_content_case_without_expectations(tmp_path: Path):
    path = _write(
        tmp_path,
        {
            "version": 1,
            "cases": [
                {
                    "id": "bad",
                    "kind": "content",
                    "question": "What?",
                    "paper_title_contains": "Joint",
                }
            ],
        },
    )
    with pytest.raises(GoldenSetError, match="expect_substrings"):
        load_golden_set(path)


def test_rejects_unknown_kind(tmp_path: Path):
    path = _write(
        tmp_path,
        {
            "version": 1,
            "cases": [
                {
                    "id": "bad",
                    "kind": "vibes",
                    "question": "What?",
                    "paper_title_contains": "X",
                    "expect_substrings": ["y"],
                }
            ],
        },
    )
    with pytest.raises(GoldenSetError, match="kind"):
        load_golden_set(path)


def test_rejects_duplicate_ids(tmp_path: Path):
    """Duplicate ids would make per-case results ambiguous in the report."""
    case = {
        "id": "dup",
        "kind": "content",
        "question": "q",
        "paper_title_contains": "X",
        "expect_substrings": ["y"],
    }
    path = _write(tmp_path, {"version": 1, "cases": [case, dict(case)]})
    with pytest.raises(GoldenSetError, match="dup"):
        load_golden_set(path)


def test_satisfies_requires_all_substrings():
    """All, not any — one common word must not carry a case."""
    case = Case(
        id="c",
        kind="content",
        question="q",
        paper_title_contains="Joint Optimization",
        expect_substrings=("revisit time", "coverage"),
    )
    title = "Joint Optimization of Connectivity, Coverage, and Revisit Time"
    assert chunk_satisfies(case, title, "we minimize revisit time and coverage gaps")
    assert not chunk_satisfies(case, title, "we minimize revisit time only")


def test_satisfies_is_case_insensitive():
    case = Case(
        id="c",
        kind="content",
        question="q",
        paper_title_contains="joint optimization",
        expect_substrings=("REVISIT TIME",),
    )
    assert chunk_satisfies(case, "Joint Optimization of X", "the revisit time metric")


def test_satisfies_requires_the_expected_paper():
    """A matching substring in the wrong paper is not a hit."""
    case = Case(
        id="c",
        kind="content",
        question="q",
        paper_title_contains="Joint Optimization",
        expect_substrings=("revisit time",),
    )
    assert not chunk_satisfies(case, "Cooperative Multi-Target Search", "revisit time")
