"""Tests for the metadata harness's golden-set loading and paper-matching.

These are pure — no database, no LLM — so they run under normal pytest.
Mirrors tests/test_evals_retrieval_golden_set.py for the sibling harness.
"""

import json
from pathlib import Path

import pytest

from app.db.models import Paper
from evals.metadata.golden import GoldenSetError, MetadataCase, load_golden_set
from evals.metadata.run_eval import _match


def _write(tmp_path: Path, payload: object) -> Path:
    p = tmp_path / "golden.json"
    p.write_text(json.dumps(payload))
    return p


# --- load_golden_set / _parse_case: valid input -------------------------------


def _write_and_load(tmp_path: Path, payload: object) -> list[MetadataCase]:
    return load_golden_set(_write(tmp_path, payload))


def test_loads_a_well_formed_case_with_values(tmp_path: Path):
    cases = _write_and_load(
        tmp_path,
        [
            {
                "paper_title_contains": "Joint Optimization",
                "authors": ["Kadircan Kara", "Evşen Yanmaz"],
                "year": 2024,
                "venue": "IEEE ICRA",
            }
        ],
    )
    assert cases == [
        MetadataCase(
            paper_title_contains="Joint Optimization",
            authors=("Kadircan Kara", "Evşen Yanmaz"),
            year=2024,
            venue="IEEE ICRA",
        )
    ]


def test_loads_a_case_with_absent_fields_staying_absent(tmp_path: Path):
    """year: null / venue: null / authors: [] must stay absent (None / ()),
    not become falsy-but-present values that a comparison could mistake for
    a real answer."""
    cases = _write_and_load(
        tmp_path,
        [
            {
                "paper_title_contains": "A Preprint",
                "authors": [],
                "year": None,
                "venue": None,
            }
        ],
    )
    case = cases[0]
    assert case.authors == ()
    assert case.year is None
    assert case.venue is None


def test_paper_title_contains_is_stripped(tmp_path: Path):
    cases = _write_and_load(
        tmp_path,
        [
            {
                "paper_title_contains": "  Joint Optimization  ",
                "authors": [],
                "year": None,
                "venue": None,
            }
        ],
    )
    assert cases[0].paper_title_contains == "Joint Optimization"


# --- load_golden_set: top-level shape ------------------------------------------


def test_rejects_a_missing_path(tmp_path: Path):
    with pytest.raises(GoldenSetError):
        load_golden_set(tmp_path / "does-not-exist.json")


def test_rejects_unreadable_json(tmp_path: Path):
    path = tmp_path / "golden.json"
    path.write_text("{not valid json")
    with pytest.raises(GoldenSetError):
        load_golden_set(path)


def test_rejects_empty_top_level_list(tmp_path: Path):
    """An empty golden set silently shrinks what's measured to nothing —
    that must be fatal, not a vacuous pass."""
    path = _write(tmp_path, [])
    with pytest.raises(GoldenSetError):
        load_golden_set(path)


def test_rejects_non_list_top_level_value(tmp_path: Path):
    path = _write(tmp_path, {"paper_title_contains": "X"})
    with pytest.raises(GoldenSetError):
        load_golden_set(path)


# --- _parse_case: per-case validation ------------------------------------------


def test_rejects_a_non_dict_case(tmp_path: Path):
    path = _write(tmp_path, ["not a dict"])
    with pytest.raises(GoldenSetError):
        load_golden_set(path)


def test_rejects_a_missing_paper_title_contains(tmp_path: Path):
    path = _write(tmp_path, [{"authors": [], "year": None, "venue": None}])
    with pytest.raises(GoldenSetError):
        load_golden_set(path)


def test_rejects_a_blank_paper_title_contains(tmp_path: Path):
    path = _write(
        tmp_path, [{"paper_title_contains": "   ", "authors": [], "year": None, "venue": None}]
    )
    with pytest.raises(GoldenSetError):
        load_golden_set(path)


def test_rejects_non_list_authors(tmp_path: Path):
    path = _write(
        tmp_path,
        [{"paper_title_contains": "X", "authors": "Kadircan Kara", "year": None, "venue": None}],
    )
    with pytest.raises(GoldenSetError):
        load_golden_set(path)


def test_rejects_a_non_string_author_in_the_list(tmp_path: Path):
    path = _write(
        tmp_path,
        [
            {
                "paper_title_contains": "X",
                "authors": ["Kadircan Kara", 123],
                "year": None,
                "venue": None,
            }
        ],
    )
    with pytest.raises(GoldenSetError):
        load_golden_set(path)


def test_rejects_a_non_int_year(tmp_path: Path):
    path = _write(
        tmp_path, [{"paper_title_contains": "X", "authors": [], "year": "2024", "venue": None}]
    )
    with pytest.raises(GoldenSetError):
        load_golden_set(path)


def test_rejects_a_boolean_year(tmp_path: Path):
    """isinstance(True, int) is True in Python — a golden-set typo like
    "year": true must not silently pass validation and become
    MetadataCase(year=True), which would then compare falsy-but-present
    against a real year and score `wrong` for a case that was never
    intentional."""
    path = _write(
        tmp_path, [{"paper_title_contains": "X", "authors": [], "year": True, "venue": None}]
    )
    with pytest.raises(GoldenSetError):
        load_golden_set(path)


def test_rejects_a_non_string_venue(tmp_path: Path):
    path = _write(
        tmp_path, [{"paper_title_contains": "X", "authors": [], "year": None, "venue": 2024}]
    )
    with pytest.raises(GoldenSetError):
        load_golden_set(path)


# --- _match ---------------------------------------------------------------------


def _paper(title: str) -> Paper:
    return Paper(title=title)


def test_match_finds_the_paper_containing_the_substring():
    case = MetadataCase(
        paper_title_contains="Joint Optimization", authors=(), year=None, venue=None
    )
    papers = [_paper("Joint Optimization of Connectivity and Coverage"), _paper("Other Paper")]
    matched = _match(case, papers)
    assert matched is papers[0]


def test_match_raises_when_no_paper_title_contains_the_substring():
    case = MetadataCase(paper_title_contains="Missing Paper", authors=(), year=None, venue=None)
    papers = [_paper("Joint Optimization"), _paper("Other Paper")]
    with pytest.raises(GoldenSetError):
        _match(case, papers)


def test_match_raises_when_more_than_one_paper_matches():
    case = MetadataCase(paper_title_contains="Paper", authors=(), year=None, venue=None)
    papers = [_paper("First Paper"), _paper("Second Paper")]
    with pytest.raises(GoldenSetError):
        _match(case, papers)
