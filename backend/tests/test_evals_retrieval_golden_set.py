"""Tests for the eval harness's golden-set loading and satisfaction predicate.

These are pure — no database, no embeddings — so they run under normal pytest.
"""

import json
from pathlib import Path

import pytest

from evals.retrieval.golden_set import Case, GoldenSetError, chunk_satisfies, load_golden_set


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
    """Duplicate ids would make per-case results ambiguous in the report.

    The fixture id and match pattern are chosen so this test proves the
    offending id actually reaches the error message: "dup" would trivially
    match because it's a substring of "duplicate", so we use an id that
    isn't, and match on the full "duplicate case id" phrasing.
    """
    case = {
        "id": "zzz-repeat",
        "kind": "content",
        "question": "q",
        "paper_title_contains": "X",
        "expect_substrings": ["y"],
    }
    path = _write(tmp_path, {"version": 1, "cases": [case, dict(case)]})
    with pytest.raises(GoldenSetError, match="duplicate case id 'zzz-repeat'"):
        load_golden_set(path)


def test_rejects_non_list_expect_substrings(tmp_path: Path):
    """A string instead of a list of substrings would explode into individual
    characters via tuple(str) — and since chunk_satisfies requires ALL of
    them, and every letter plus space is present in ordinary prose, the case
    would silently become a match-anything trap instead of raising."""
    path = _write(
        tmp_path,
        {
            "version": 1,
            "cases": [
                {
                    "id": "bad",
                    "kind": "content",
                    "question": "q",
                    "paper_title_contains": "X",
                    "expect_substrings": "revisit time",
                }
            ],
        },
    )
    with pytest.raises(GoldenSetError, match="expect_substrings"):
        load_golden_set(path)


def test_rejects_non_dict_case_entries(tmp_path: Path):
    """A cases list containing a non-dict entry must raise GoldenSetError —
    not an incidental AttributeError from the parser reaching into it, which
    would break the documented contract that malformed input always raises
    GoldenSetError."""
    path = _write(tmp_path, {"version": 1, "cases": ["oops"]})
    with pytest.raises(GoldenSetError, match="case"):
        load_golden_set(path)


def test_rejects_whitespace_only_title(tmp_path: Path):
    """A lone space passes a truthiness check but is a substring of virtually
    every multi-word title, making the "requires the expected paper" guard
    vacuous for that case."""
    path = _write(
        tmp_path,
        {
            "version": 1,
            "cases": [
                {
                    "id": "bad",
                    "kind": "content",
                    "question": "q",
                    "paper_title_contains": "   ",
                    "expect_substrings": ["y"],
                }
            ],
        },
    )
    with pytest.raises(GoldenSetError, match="title_contains"):
        load_golden_set(path)


def test_rejects_empty_string_expect_substrings_element(tmp_path: Path):
    """An empty-string element is a substring of every chunk, so
    chunk_satisfies would return True for any chunk from the correct paper
    regardless of content -- the same silent-false-hit class as the
    non-list expect_substrings bug, just per-element instead of per-field."""
    path = _write(
        tmp_path,
        {
            "version": 1,
            "cases": [
                {
                    "id": "bad",
                    "kind": "content",
                    "question": "q",
                    "paper_title_contains": "X",
                    "expect_substrings": [""],
                }
            ],
        },
    )
    with pytest.raises(GoldenSetError, match=r"expect_substrings\[0\]"):
        load_golden_set(path)


def test_rejects_whitespace_only_expect_substrings_element(tmp_path: Path):
    """Same class as the empty-string element, but whitespace-only -- still
    a substring of every chunk."""
    path = _write(
        tmp_path,
        {
            "version": 1,
            "cases": [
                {
                    "id": "bad",
                    "kind": "content",
                    "question": "q",
                    "paper_title_contains": "X",
                    "expect_substrings": ["revisit time", " "],
                }
            ],
        },
    )
    with pytest.raises(GoldenSetError, match=r"expect_substrings\[1\]"):
        load_golden_set(path)


def test_rejects_non_string_expect_substrings_element(tmp_path: Path):
    """isinstance(sub, str) must actually be exercised, not just eyeballed --
    an int element (e.g. a stray unquoted number from hand-edited JSON) must
    raise rather than reach chunk_satisfies, where `sub.lower()` would blow
    up with an unhandled AttributeError deep inside a scoring run instead of
    failing loudly at load time."""
    path = _write(
        tmp_path,
        {
            "version": 1,
            "cases": [
                {
                    "id": "bad",
                    "kind": "content",
                    "question": "q",
                    "paper_title_contains": "X",
                    "expect_substrings": [123],
                }
            ],
        },
    )
    with pytest.raises(GoldenSetError, match=r"expect_substrings\[0\]"):
        load_golden_set(path)


def test_expect_substrings_elements_are_stored_stripped(tmp_path: Path):
    """Regression test for the stripping decision in _parse_case: a
    surrounding-whitespace substring must be stored stripped, and must then
    match chunk text where the phrase sits right at a boundary (nothing
    after it) -- the case `.strip()` exists to fix. Without the strip, the
    loaded Case would keep the padding, and chunk_satisfies would look for
    "  revisit time  " (with padding) inside lowercased chunk text, which a
    phrase ending a string never contains."""
    path = _write(
        tmp_path,
        {
            "version": 1,
            "cases": [
                {
                    "id": "c1",
                    "kind": "content",
                    "question": "q",
                    "paper_title_contains": "Joint Optimization",
                    "expect_substrings": ["  revisit time  "],
                }
            ],
        },
    )
    cases = load_golden_set(path)
    assert cases[0].expect_substrings == ("revisit time",)
    assert chunk_satisfies(cases[0], "Joint Optimization of X", "we aim to minimize revisit time")


def test_loads_a_case_with_multiple_valid_substrings(tmp_path: Path):
    """Both-directions check for the element-validation fix above: a
    legitimate multi-element expect_substrings list must still load."""
    path = _write(
        tmp_path,
        {
            "version": 1,
            "cases": [
                {
                    "id": "c1",
                    "kind": "content",
                    "question": "q",
                    "paper_title_contains": "Joint Optimization",
                    "expect_substrings": ["revisit time", "coverage"],
                }
            ],
        },
    )
    cases = load_golden_set(path)
    assert cases[0].expect_substrings == ("revisit time", "coverage")


def test_rejects_dict_valued_cases(tmp_path: Path):
    """'cases' must be a list. A dict (e.g. mistakenly keyed by case id) is
    truthy but not a list, and must be rejected explicitly -- covers the
    'cases' must be a list' branch that the non-dict-entry test doesn't."""
    path = _write(
        tmp_path,
        {"version": 1, "cases": {"c1": {"id": "c1", "kind": "content"}}},
    )
    with pytest.raises(GoldenSetError, match="'cases' must be a list"):
        load_golden_set(path)


def test_satisfies_requires_all_substrings():
    """All, not any — one common word must not carry a case."""
    from evals.retrieval.golden_set import PaperExpectation

    case = Case(
        id="c",
        kind="content",
        question="q",
        expect_papers=(
            PaperExpectation(
                title_contains="Joint Optimization",
                expect_substrings=("revisit time", "coverage"),
            ),
        ),
    )
    title = "Joint Optimization of Connectivity, Coverage, and Revisit Time"
    assert chunk_satisfies(case, title, "we minimize revisit time and coverage gaps")
    assert not chunk_satisfies(case, title, "we minimize revisit time only")


def test_satisfies_is_case_insensitive():
    from evals.retrieval.golden_set import PaperExpectation

    case = Case(
        id="c",
        kind="content",
        question="q",
        expect_papers=(
            PaperExpectation(
                title_contains="joint optimization",
                expect_substrings=("REVISIT TIME",),
            ),
        ),
    )
    assert chunk_satisfies(case, "Joint Optimization of X", "the revisit time metric")


def test_satisfies_requires_the_expected_paper():
    """A matching substring in the wrong paper is not a hit."""
    from evals.retrieval.golden_set import PaperExpectation

    case = Case(
        id="c",
        kind="content",
        question="q",
        expect_papers=(
            PaperExpectation(
                title_contains="Joint Optimization",
                expect_substrings=("revisit time",),
            ),
        ),
    )
    assert not chunk_satisfies(case, "Cooperative Multi-Target Search", "revisit time")


# New tests for multi-paper expectations (Task 10)


def test_a_scalar_case_parses_into_one_expectation():
    from evals.retrieval.golden_set import PaperExpectation, _parse_case

    case = _parse_case(
        {
            "id": "c1",
            "kind": "content",
            "question": "q",
            "paper_title_contains": "Deadly Triad",
            "expect_substrings": ["clipped double-Q"],
        }
    )
    assert case.expect_papers == (
        PaperExpectation(title_contains="Deadly Triad", expect_substrings=("clipped double-Q",)),
    )
    assert case.paper_title_contains == "Deadly Triad"
    assert case.expect_substrings == ("clipped double-Q",)


def test_a_multi_paper_case_parses_every_expectation():
    from evals.retrieval.golden_set import _parse_case

    case = _parse_case(
        {
            "id": "c2",
            "kind": "content",
            "question": "compare",
            "expect_papers": [
                {"title_contains": "Lazy Agents", "expect_substrings": ["potential-based"]},
                {"title_contains": "Deadly Triad", "expect_substrings": ["clipped double-Q"]},
            ],
        }
    )
    assert [e.title_contains for e in case.expect_papers] == ["Lazy Agents", "Deadly Triad"]


def test_scalar_and_list_forms_together_are_rejected():
    from evals.retrieval.golden_set import _parse_case

    with pytest.raises(GoldenSetError, match="both"):
        _parse_case(
            {
                "id": "c3",
                "kind": "content",
                "question": "q",
                "paper_title_contains": "A",
                "expect_substrings": ["x"],
                "expect_papers": [{"title_contains": "B", "expect_substrings": ["y"]}],
            }
        )


def test_an_expectation_without_substrings_is_rejected():
    from evals.retrieval.golden_set import _parse_case

    with pytest.raises(GoldenSetError, match="expect_substrings"):
        _parse_case(
            {
                "id": "c4",
                "kind": "content",
                "question": "q",
                "expect_papers": [{"title_contains": "A", "expect_substrings": []}],
            }
        )


def test_off_topic_still_rejects_expect_papers():
    from evals.retrieval.golden_set import _parse_case

    with pytest.raises(GoldenSetError, match="off_topic"):
        _parse_case(
            {
                "id": "c5",
                "kind": "off_topic",
                "question": "q",
                "expect_papers": [{"title_contains": "A", "expect_substrings": ["x"]}],
            }
        )


def test_substrings_are_stripped_in_the_list_form():
    from evals.retrieval.golden_set import _parse_case

    case = _parse_case(
        {
            "id": "c6",
            "kind": "content",
            "question": "q",
            "expect_papers": [{"title_contains": "A", "expect_substrings": ["  revisit time "]}],
        }
    )
    assert case.expect_papers[0].expect_substrings == ("revisit time",)
