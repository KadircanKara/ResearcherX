"""Pure tests for the resolver harness — no database, no embeddings, no LLM."""

import json
from pathlib import Path

import pytest

from evals.retrieval.resolver_eval import (
    ResolverCase,
    ResolverSetError,
    load_resolver_set,
    resolution_matches,
)

_SHIPPED = Path(__file__).parent.parent / "evals" / "retrieval" / "resolver_set.json"


def _write(tmp_path: Path, payload: dict) -> Path:
    p = tmp_path / "resolver.json"
    p.write_text(json.dumps(payload))
    return p


def test_the_shipped_set_loads_and_keeps_its_negatives():
    """The negatives are the point: they assert the resolver FALLS THROUGH
    rather than guessing, which is the only reason it is allowed to exist
    after the LLM targeter was deleted for guessing."""
    cases = load_resolver_set(_SHIPPED)

    assert len(cases) >= 8
    assert any(c.is_negative for c in cases)
    assert len({c.id for c in cases}) == len(cases)


def test_a_duplicate_case_id_is_rejected(tmp_path: Path):
    payload = {
        "cases": [
            {"id": "same", "question": "q1", "expect_titles": []},
            {"id": "same", "question": "q2", "expect_titles": []},
        ]
    }

    with pytest.raises(ResolverSetError, match="duplicate case id"):
        load_resolver_set(_write(tmp_path, payload))


def test_a_missing_expect_titles_is_rejected_rather_than_assumed_negative(tmp_path: Path):
    """Absent is not the same as empty. An omitted field is a malformed case;
    silently reading it as "must fall through" would score a broken case as a
    pass."""
    payload = {"cases": [{"id": "c1", "question": "q"}]}

    with pytest.raises(ResolverSetError, match="expect_titles must be a list"):
        load_resolver_set(_write(tmp_path, payload))


def test_an_empty_file_is_rejected(tmp_path: Path):
    with pytest.raises(ResolverSetError, match="no cases defined"):
        load_resolver_set(_write(tmp_path, {"cases": []}))


def test_expect_titles_are_stripped(tmp_path: Path):
    payload = {"cases": [{"id": "c1", "question": "q", "expect_titles": ["  Padded Title  "]}]}

    assert load_resolver_set(_write(tmp_path, payload))[0].expect_titles == ("Padded Title",)


def test_a_negative_case_passes_only_on_an_empty_resolution():
    case = ResolverCase("neg", "q", ())

    assert resolution_matches(case, [])
    assert not resolution_matches(case, ["Any Paper At All"])


def test_resolving_an_extra_paper_is_a_failure_not_a_partial_pass():
    """Set equality, not containment. Within a fixed budget an extra paper's
    chunks displace the answer's, so "right paper plus one" is a wrong scope."""
    case = ResolverCase("pos", "q", ("Cooperative Multi-Target Search",))

    assert resolution_matches(case, ["Cooperative Multi-Target Search with UAV Swarms"])
    assert not resolution_matches(
        case, ["Cooperative Multi-Target Search with UAV Swarms", "Some Other Paper"]
    )


def test_matching_is_order_insensitive_across_two_expected_papers():
    case = ResolverCase("pair", "q", ("Alpha Paper", "Beta Paper"))

    assert resolution_matches(case, ["Beta Paper on Swarms", "Alpha Paper on Drones"])


def test_a_missing_expected_paper_fails():
    case = ResolverCase("pair", "q", ("Alpha Paper", "Beta Paper"))

    assert not resolution_matches(case, ["Alpha Paper on Drones", "Gamma Paper"])
