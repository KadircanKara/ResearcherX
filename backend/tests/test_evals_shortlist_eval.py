"""Pure tests for the scoping harness — no database, no embeddings, no LLM."""

import json
from pathlib import Path

from evals.retrieval.shortlist_eval import ScopeCase, _load_cases, _resolve

_SCOPE_SET = Path(__file__).parent.parent / "evals" / "retrieval" / "scope_set.json"


def test_the_shipped_scope_set_parses_and_has_unique_ids():
    cases = json.loads(_SCOPE_SET.read_text())["cases"]
    assert cases
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids))
    for case in cases:
        assert case["question"].strip()
        assert case["paper_title_contains"].strip()


def test_load_cases_keeps_positives_and_drops_off_topic(tmp_path: Path):
    golden = tmp_path / "golden.json"
    golden.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "c1",
                        "kind": "content",
                        "question": "q1",
                        "paper_title_contains": "Paper One",
                    },
                    {"id": "neg", "kind": "off_topic", "question": "cake recipe"},
                ]
            }
        )
    )
    scope = tmp_path / "scope.json"
    scope.write_text(
        json.dumps({"cases": [{"id": "s1", "question": "q2", "paper_title_contains": "Paper Two"}]})
    )

    loaded = _load_cases(golden, scope)

    assert [c.id for c in loaded["golden"]] == ["c1"]
    assert [c.id for c in loaded["scope"]] == ["s1"]


def test_resolve_matches_one_title_case_insensitively():
    case = ScopeCase("c", "q", "cooperative multi-target")
    titles = {"p1": "Cooperative Multi-Target Search with UAV Swarms", "p2": "Something Else"}

    assert _resolve(case, titles) == "p1"


def test_an_ambiguous_title_marker_is_unusable_not_guessed():
    """Two matches means the marker stopped identifying a paper on this
    corpus. Scoring it against an arbitrary one would report a targeter
    failure that is really a stale case."""
    case = ScopeCase("c", "q", "federated learning")
    titles = {"p1": "Federated Learning in the Sky", "p2": "Federated Learning for UAVs"}

    assert _resolve(case, titles) is None


def test_a_marker_that_matches_nothing_is_unusable():
    case = ScopeCase("c", "q", "a paper that was deleted")

    assert _resolve(case, {"p1": "Cooperative Multi-Target Search"}) is None
