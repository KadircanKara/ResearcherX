"""Citation attribution enforcement — the strip pass and its failure envelope.

Every test here checks one direction of the same asymmetry: enforcement is
allowed to MISS a misattribution (status quo), never to strip a valid marker
(new harm).
"""

import json
from pathlib import Path

from app.services.citation_attribution import strip_misattributed_citations

# Two library papers. COOP is the one the live turn actually retrieved from.
COOP = "985378c1"
DEEP_RL = "b1b1b1b1"
FEDERATED = "c2c2c2c2"
HARVEST = "d3d3d3d3"

TITLES = {
    COOP: "Cooperative Multi-Target Search with UAV Swarms",
    DEEP_RL: "Deep Reinforcement Learning for Trajectory Path Planning",
    FEDERATED: "Federated Learning in the Sky: Aerial Networks",
    HARVEST: "Multi-UAV Path Planning for Wireless Data Harvesting",
}

# The live turn's evidence: five chunks, all from one paper.
ALL_COOP = {n: COOP for n in range(1, 6)}


def test_flowing_prose_with_no_list_heads_is_byte_identical():
    text = (
        "The planner rewards coverage and penalises collisions [1], and the "
        "Cooperative Multi-Target Search with UAV Swarms paper reports [2].\n"
    )
    cleaned, stripped = strip_misattributed_citations(
        text, chunk_papers={1: DEEP_RL, 2: COOP}, paper_titles=TITLES
    )
    assert cleaned == text
    assert stripped == []


def test_a_marker_inside_a_fenced_block_inside_a_span_is_untouched():
    text = "1. **Title:** Cooperative Multi-Target Search with UAV Swarms\n```python\narr[4]\n```\n"
    cleaned, stripped = strip_misattributed_citations(
        text, chunk_papers={4: DEEP_RL}, paper_titles=TITLES
    )
    assert cleaned == text
    assert stripped == []


def test_a_marker_inside_an_inline_code_span_inside_a_span_is_untouched():
    text = "- Cooperative Multi-Target Search with UAV Swarms indexes `arr[4]` here.\n"
    cleaned, stripped = strip_misattributed_citations(
        text, chunk_papers={4: DEEP_RL}, paper_titles=TITLES
    )
    assert cleaned == text
    assert stripped == []


def test_a_span_whose_head_paper_owns_its_markers_keeps_every_one():
    text = "1. Cooperative Multi-Target Search with UAV Swarms: covered here [1], [2], [3].\n"
    cleaned, stripped = strip_misattributed_citations(
        text, chunk_papers=ALL_COOP, paper_titles=TITLES
    )
    assert cleaned == text
    assert stripped == []


def test_prose_before_the_first_span_head_is_untouched():
    text = (
        "Three papers matter here [2].\n\n- Cooperative Multi-Target Search with UAV Swarms [1].\n"
    )
    cleaned, stripped = strip_misattributed_citations(
        text, chunk_papers={1: COOP, 2: DEEP_RL}, paper_titles=TITLES
    )
    assert cleaned == text
    assert stripped == []


def test_a_tie_between_two_titles_opens_no_span():
    titles = {
        "a": "Multi-UAV Path Planning for Wireless Data Harvesting",
        "b": "Multi-UAV Path Planning for Energy-Aware Coverage",
    }
    text = "1. Multi-UAV Path Planning for something else entirely [1].\n"
    cleaned, stripped = strip_misattributed_citations(
        text, chunk_papers={1: COOP}, paper_titles=titles
    )
    assert cleaned == text
    assert stripped == []


def test_a_title_paraphrase_below_the_four_word_anchor_opens_no_span():
    text = "1. The Cooperative Search paper describes the reward shaping [1].\n"
    cleaned, stripped = strip_misattributed_citations(
        text, chunk_papers={1: DEEP_RL}, paper_titles=TITLES
    )
    assert cleaned == text
    assert stripped == []


def test_an_inline_title_mention_opens_no_span():
    text = (
        "Cooperative Multi-Target Search with UAV Swarms is one of them, and so "
        "is the harvesting work [1].\n"
    )
    cleaned, stripped = strip_misattributed_citations(
        text, chunk_papers={1: DEEP_RL}, paper_titles=TITLES
    )
    assert cleaned == text
    assert stripped == []


def test_a_list_item_naming_no_paper_closes_the_previous_span():
    text = (
        "1. Cooperative Multi-Target Search with UAV Swarms [1].\n"
        "2. Some unrelated observation [2].\n"
    )
    cleaned, stripped = strip_misattributed_citations(
        text, chunk_papers={1: COOP, 2: DEEP_RL}, paper_titles=TITLES
    )
    assert cleaned == text
    assert stripped == []


def test_an_out_of_range_marker_is_left_for_renumbering_to_replace():
    text = "1. Cooperative Multi-Target Search with UAV Swarms [9].\n"
    cleaned, stripped = strip_misattributed_citations(
        text, chunk_papers=ALL_COOP, paper_titles=TITLES
    )
    assert cleaned == text
    assert stripped == []


def test_a_whole_run_of_invalid_markers_takes_its_binding_separators_with_it():
    text = "1. Federated Learning in the Sky: Aerial Networks cover hotspots [2], [3].\n"
    cleaned, stripped = strip_misattributed_citations(
        text, chunk_papers=ALL_COOP, paper_titles=TITLES
    )
    assert cleaned == "1. Federated Learning in the Sky: Aerial Networks cover hotspots.\n"
    assert stripped == [2, 3]


def test_a_partially_valid_run_keeps_its_survivors_and_its_spacing():
    text = "1. Cooperative Multi-Target Search with UAV Swarms: shown here [1], [2].\n"
    cleaned, stripped = strip_misattributed_citations(
        text, chunk_papers={1: COOP, 2: DEEP_RL}, paper_titles=TITLES
    )
    assert cleaned == "1. Cooperative Multi-Target Search with UAV Swarms: shown here [1].\n"
    assert stripped == [2]


def test_a_dangling_conjunction_is_left_alone_rather_than_repaired():
    # Documented cosmetic edge: this pass is a redaction, not an editor.
    text = "1. Federated Learning in the Sky: Aerial Networks, shown in [3] and [1].\n"
    cleaned, stripped = strip_misattributed_citations(
        text, chunk_papers={3: COOP, 1: FEDERATED}, paper_titles=TITLES
    )
    assert cleaned == "1. Federated Learning in the Sky: Aerial Networks, shown in and [1].\n"
    assert stripped == [3]


# --- Golden regression from conversation 0ade8377-53b0-4f9a-bbad-a2ab00c961a7 -
#
# The REAL turn-2 answer and its REAL five-entry citation array, extracted from
# the dev database into tests/fixtures/citation_attribution_live_turn.json
# along with all 100 of that project's paper titles. This is the actual bug,
# not a reconstruction of it: five chunks retrieved, ALL from paper 985378c1
# (Cooperative Multi-Target Search), and an answer enumerating four papers with
# markers hung off all four.
#
# The stored text is post-renumbering, so its markers read 1..5 and
# chunk_papers is keyed to match — all the strip needs. In production the pass
# runs on pre-renumbering catalog positions; the ordering itself is pinned by
# test_a_misattributed_marker_is_stripped_before_the_citation_array_is_built.
LIVE = json.loads(
    (Path(__file__).parent / "fixtures" / "citation_attribution_live_turn.json").read_text()
)
LIVE_CHUNK_PAPERS = {int(n): paper_id for n, paper_id in LIVE["chunk_papers"].items()}


def test_the_live_misattribution_is_stripped_item_by_item():
    cleaned, stripped = strip_misattributed_citations(
        LIVE["answer"],
        chunk_papers=LIVE_CHUNK_PAPERS,
        paper_titles=LIVE["paper_titles"],
    )
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]

    # Item 1 is headed by the paper that actually owns all five chunks.
    assert any(line.endswith("detailed here [1], [2], [3], [4], [5].") for line in lines)

    # Items 2-4 are headed by papers with no chunk in this turn's evidence at
    # all. Their trailing markers go; the sentences keep their punctuation.
    for tail in (
        "motivating UAVs to service demand hotspots.",
        "to optimize federated learning with UAV swarms.",
        "reflecting standard deep RL reward paradigms.",
    ):
        assert any(line.endswith(tail) for line in lines), tail

    assert sorted(stripped) == [2, 3, 4, 5]
    # The claims survive; only the false provenance goes.
    assert "penalties for violations and delays" in cleaned
