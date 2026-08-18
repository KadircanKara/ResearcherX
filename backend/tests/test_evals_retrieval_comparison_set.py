"""Tests for the two-paper COMPARISON set's loading and per-side projection.

Pure — no database, no embeddings — except for the shipped-file tests, which
only parse `comparison_set.json` off disk. Substring-vs-corpus verification is
NOT done here (it needs Postgres and the embedding model); it is done by the
harness itself, which routes an unwinnable case to `ERRORS` rather than
scoring it as a retrieval miss.
"""

import json
from pathlib import Path

import pytest

from evals.retrieval.comparison_set import (
    ComparisonSetError,
    load_comparison_set,
)
from evals.retrieval.golden_set import chunk_satisfies

_SHIPPED = Path(__file__).resolve().parents[1] / "evals" / "retrieval" / "comparison_set.json"


def _case(**overrides) -> dict:
    base = {
        "id": "c1",
        "question": "How do X and Y differ in Z?",
        "paper_a_title_contains": "SwarmLab",
        "expect_a_substrings": ["entirely written in MATLAB"],
        "paper_b_title_contains": "XTDrone",
        "expect_b_substrings": ["based on ROS, Gazebo and PX4"],
    }
    base.update(overrides)
    return base


def _write(tmp_path: Path, *cases: dict) -> Path:
    p = tmp_path / "cs.json"
    p.write_text(json.dumps({"version": 1, "cases": list(cases)}))
    return p


def test_loads_a_comparison_case(tmp_path: Path):
    cases = load_comparison_set(_write(tmp_path, _case()))
    assert len(cases) == 1
    case = cases[0]
    assert case.id == "c1"
    assert case.a_title_contains == "SwarmLab"
    assert case.b_expect_substrings == ("based on ROS, Gazebo and PX4",)
    assert case.sides == ("a", "b")


def test_side_projects_into_a_scorable_golden_set_case(tmp_path: Path):
    """The whole point of `side()`: one half of a comparison must score with
    the SHARED predicate, so the two files cannot drift apart on matching."""
    case = load_comparison_set(_write(tmp_path, _case()))[0]
    side_a, side_b = case.side("a"), case.side("b")

    assert side_a.id == "c1:a"
    assert side_a.is_negative is False
    assert chunk_satisfies(
        side_a, "SwarmLab: a Matlab Drone Swarm Simulator", "SOFTWARE ENTIRELY WRITTEN IN MATLAB"
    )
    # A's substring inside B's paper is not a hit for A — the paper gate holds.
    assert not chunk_satisfies(
        side_a, "XTDrone: A Customizable Platform", "entirely written in MATLAB"
    )
    assert chunk_satisfies(
        side_b,
        "XTDrone: A Customizable Platform",
        "a platform based on ROS, Gazebo and PX4 is presented",
    )


def test_side_rejects_an_unknown_side(tmp_path: Path):
    case = load_comparison_set(_write(tmp_path, _case()))[0]
    with pytest.raises(ValueError, match="side must be"):
        case.side("c")


def test_rejects_two_needles_naming_the_same_paper(tmp_path: Path):
    """A case whose two needles resolve to ONE paper is a single-paper case in
    disguise: every representation metric would report a perfect score while
    measuring nothing the single-scope harness doesn't already cover."""
    path = _write(
        tmp_path,
        _case(paper_a_title_contains="SwarmLab", paper_b_title_contains="SwarmLab"),
    )
    with pytest.raises(ComparisonSetError, match="two different papers"):
        load_comparison_set(path)


def test_rejects_needles_that_are_distinct_strings_but_nest(tmp_path: Path):
    """Containment, not equality, is the test: 'Partial Replanning' and
    'Partial Replanning for Decentralized Dynamic Task Allocation' are two
    different strings naming one paper."""
    path = _write(
        tmp_path,
        _case(
            paper_a_title_contains="Partial Replanning",
            paper_b_title_contains="Partial Replanning for Decentralized Dynamic Task Allocation",
        ),
    )
    with pytest.raises(ComparisonSetError, match="not distinct"):
        load_comparison_set(path)


def test_the_distinctness_check_is_case_insensitive(tmp_path: Path):
    """`chunk_satisfies` matches case-insensitively, so 'swarmlab' and
    'SwarmLab' resolve to the same paper and must be rejected the same way."""
    path = _write(
        tmp_path,
        _case(paper_a_title_contains="swarmlab", paper_b_title_contains="SwarmLab"),
    )
    with pytest.raises(ComparisonSetError, match="not distinct"):
        load_comparison_set(path)


@pytest.mark.parametrize(
    "field", ["id", "question", "paper_a_title_contains", "paper_b_title_contains"]
)
def test_rejects_missing_required_string_fields(tmp_path: Path, field: str):
    raw = _case()
    del raw[field]
    with pytest.raises(ComparisonSetError, match=field):
        load_comparison_set(_write(tmp_path, raw))


@pytest.mark.parametrize("field", ["paper_a_title_contains", "paper_b_title_contains"])
def test_rejects_whitespace_only_title(tmp_path: Path, field: str):
    """A lone space is a substring of virtually every multi-word title, which
    makes the 'requires the expected paper' guard vacuous for that side."""
    with pytest.raises(ComparisonSetError, match=field):
        load_comparison_set(_write(tmp_path, _case(**{field: "   "})))


@pytest.mark.parametrize("field", ["expect_a_substrings", "expect_b_substrings"])
def test_rejects_missing_expectations_for_either_side(tmp_path: Path, field: str):
    """A comparison case with expectations for only one side cannot answer the
    question it asks — the missing side would score as trivially absent."""
    raw = _case()
    del raw[field]
    with pytest.raises(ComparisonSetError, match=field):
        load_comparison_set(_write(tmp_path, raw))


@pytest.mark.parametrize("field", ["expect_a_substrings", "expect_b_substrings"])
def test_rejects_empty_expectations_for_either_side(tmp_path: Path, field: str):
    with pytest.raises(ComparisonSetError, match="must not be empty"):
        load_comparison_set(_write(tmp_path, _case(**{field: []})))


@pytest.mark.parametrize("field", ["expect_a_substrings", "expect_b_substrings"])
def test_rejects_non_list_expectations(tmp_path: Path, field: str):
    """A bare string would explode into single characters via tuple(str), and
    since `chunk_satisfies` requires ALL of them and ordinary prose contains
    every letter, the side would silently become a match-anything trap."""
    with pytest.raises(ComparisonSetError, match=field):
        load_comparison_set(_write(tmp_path, _case(**{field: "revisit time"})))


@pytest.mark.parametrize("bad", [[""], ["  "], [123], ["ok", ""]])
def test_rejects_bad_substring_elements(tmp_path: Path, bad):
    """Empty and whitespace-only elements are substrings of every chunk; a
    non-string element would blow up with AttributeError deep inside scoring
    instead of failing loudly at load time."""
    with pytest.raises(ComparisonSetError, match=r"expect_a_substrings\[\d\]"):
        load_comparison_set(_write(tmp_path, _case(expect_a_substrings=bad)))


def test_substrings_are_stored_stripped(tmp_path: Path):
    """Same regression guard as the golden set's: padding copied from a PDF
    must not survive into the predicate, where a phrase ending a chunk would
    never match."""
    case = load_comparison_set(
        _write(tmp_path, _case(expect_a_substrings=["  entirely written in MATLAB  "]))
    )[0]
    assert case.a_expect_substrings == ("entirely written in MATLAB",)
    assert chunk_satisfies(case.side("a"), "SwarmLab", "a package entirely written in MATLAB")


def test_rejects_duplicate_ids(tmp_path: Path):
    """Duplicate ids make per-case results ambiguous in the report. The id is
    chosen so the match cannot pass by being a substring of 'duplicate'."""
    path = _write(tmp_path, _case(id="zzz-repeat"), _case(id="zzz-repeat"))
    with pytest.raises(ComparisonSetError, match="duplicate case id 'zzz-repeat'"):
        load_comparison_set(path)


def test_rejects_non_dict_case_entries(tmp_path: Path):
    path = tmp_path / "cs.json"
    path.write_text(json.dumps({"version": 1, "cases": ["oops"]}))
    with pytest.raises(ComparisonSetError, match="case"):
        load_comparison_set(path)


def test_rejects_dict_valued_cases(tmp_path: Path):
    path = tmp_path / "cs.json"
    path.write_text(json.dumps({"version": 1, "cases": {"c1": {"id": "c1"}}}))
    with pytest.raises(ComparisonSetError, match="'cases' must be a list"):
        load_comparison_set(path)


def test_rejects_empty_set(tmp_path: Path):
    path = tmp_path / "cs.json"
    path.write_text(json.dumps({"version": 1, "cases": []}))
    with pytest.raises(ComparisonSetError, match="no cases defined"):
        load_comparison_set(path)


def test_the_shipped_comparison_set_parses():
    """The file that ships is the ground truth future measurements are scored
    against — a malformed one must fail here, not mid-run."""
    cases = load_comparison_set(_SHIPPED)
    assert len(cases) >= 6, "fewer than six real comparison cases is not a measurable arm"
    assert len({c.id for c in cases}) == len(cases)


def test_every_shipped_case_names_two_different_papers():
    """Belt and braces over the loader's own check: this asserts the property
    on the SHIPPED data, so a future hand-edit that pairs a paper with itself
    fails here even if the loader's guard is ever loosened."""
    for case in load_comparison_set(_SHIPPED):
        a, b = case.a_title_contains.lower(), case.b_title_contains.lower()
        assert a != b, case.id
        assert a not in b and b not in a, case.id


def test_every_shipped_case_carries_expectations_for_both_sides():
    for case in load_comparison_set(_SHIPPED):
        assert case.a_expect_substrings, case.id
        assert case.b_expect_substrings, case.id
        for side in case.sides:
            projected = case.side(side)
            assert projected.paper_title_contains
            assert projected.expect_substrings
