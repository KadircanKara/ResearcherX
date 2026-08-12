"""Intra-paper and multi-paper chunk selection policies, in isolation.

The functions are pure so both production (chat_service) and the eval harness
can call the SAME policy — a re-implementation on either side would drift
silently, and the whole point of the cuts is that they are measurable.
"""

from app.services.intra_paper_ranker import (
    admit_papers,
    keep_within_paper,
    merge_across_papers,
)


def test_empty_input_keeps_nothing():
    assert keep_within_paper([], delta=0.25) == 0


def test_single_distance_is_always_kept():
    """The nearest chunk is kept whatever the delta: it defines the baseline,
    so it can never be further than delta from itself."""
    assert keep_within_paper([0.9], delta=0.0) == 1


def test_keeps_every_distance_inside_the_delta():
    assert keep_within_paper([0.30, 0.40, 0.50], delta=0.25) == 3


def test_cuts_at_the_first_distance_beyond_the_delta():
    assert keep_within_paper([0.30, 0.40, 0.60, 0.70], delta=0.25) == 2


def test_boundary_is_inclusive():
    """A distance exactly at best + delta is KEPT. The measured
    ground-control-station case sat 0.164 from its paper's best, and an
    exclusive boundary would make the constant's meaning depend on float
    representation rather than on the measurement."""
    assert keep_within_paper([0.50, 0.75], delta=0.25) == 2


def test_ties_at_the_boundary_are_all_kept():
    assert keep_within_paper([0.50, 0.75, 0.75], delta=0.25) == 3


def test_zero_delta_keeps_only_distances_equal_to_the_best():
    assert keep_within_paper([0.40, 0.40, 0.41], delta=0.0) == 2


def test_cut_is_a_prefix_even_if_later_distances_come_back_inside():
    """Input is distance-ASCENDING by contract, so this cannot happen from
    SQL — but if a caller ever passes an unsorted list, the function must
    stop at the first violation rather than silently keeping a chunk that
    sits behind an excluded one."""
    assert keep_within_paper([0.30, 0.90, 0.35], delta=0.25) == 1


def test_intra_paper_constants_are_settings_not_literals():
    """Both numbers are embedding-model-specific, exactly like
    similarity_threshold — measured on text-embedding-3-small and invalid for
    any other embedding model. They must be env-tunable without a code change.

    The ordering assertion is the real content: a ceiling below the delta band
    would make the delta dead code. The original evidence for the upper bound
    (three off_topic cases where 0.90 admitted 1, 2 and 9 chunks) is NOT the
    full picture and config.py now explicitly retracts it as too easy: nine
    later near-domain negatives (drone regulation, insurance, hobbyist gear)
    land at 0.547-0.652, comfortably inside 0.85 itself, and a mis-targeted
    one keeps 9-52 chunks of the wrong paper at the shipped delta (see
    evals/retrieval/README.md, "Open finding"). 0.85 is a known-loose,
    open-follow-up constant, not a settled upper bound — this test only pins
    that ceiling and delta stay ordered and within the range measured so far.
    """
    from app.core.config import settings

    assert 0.80 <= settings.intra_paper_ceiling <= 0.85
    assert 0.0 < settings.intra_paper_delta < settings.intra_paper_ceiling


def test_admit_keeps_papers_under_the_threshold():
    admitted, rejected = admit_papers({"a": 0.31, "b": 0.34}, threshold=0.75)
    assert admitted == ["a", "b"]
    assert rejected == []


def test_admit_rejects_a_paper_at_or_above_the_threshold():
    admitted, rejected = admit_papers({"a": 0.31, "b": 0.7518}, threshold=0.75)
    assert admitted == ["a"]
    assert rejected == ["b"]


def test_admit_rejects_a_paper_exactly_at_the_threshold():
    """`<` not `<=`, matching the strict comparison in both SQL sites."""
    admitted, rejected = admit_papers({"a": 0.75}, threshold=0.75)
    assert admitted == []
    assert rejected == ["a"]


def test_admit_sorts_admitted_by_best_distance():
    admitted, _ = admit_papers({"far": 0.60, "near": 0.20, "mid": 0.40}, threshold=0.75)
    assert admitted == ["near", "mid", "far"]


def test_admit_handles_an_empty_mapping():
    assert admit_papers({}, threshold=0.75) == ([], [])


def test_merge_guarantees_the_floor_to_every_paper():
    """THE property the floor phase exists for, and it needs SEPARATED distance
    ranges to be testable: with overlapping ranges a plain global top-k keeps
    the farther paper anyway and the test passes against a merge that has no
    floor phase at all. Here every one of b's chunks is farther than a's 60th,
    so only the round-robin phase can get b into the budget."""
    counts = merge_across_papers(
        {
            "a": [0.10 + 0.001 * i for i in range(80)],
            "b": [0.60 + 0.001 * i for i in range(20)],
        },
        budget=60,
        floor=5,
    )
    assert counts == {"a": 55, "b": 5}


def test_merge_gives_the_remainder_to_the_nearer_paper():
    """After the floor is met, distance decides -- A's chunks are all nearer
    than B's, so A takes everything left."""
    counts = merge_across_papers(
        {"a": [0.10 + 0.001 * i for i in range(50)], "b": [0.60 + 0.001 * i for i in range(50)]},
        budget=20,
        floor=5,
    )
    assert counts == {"a": 15, "b": 5}


def test_a_high_floor_produces_parity():
    counts = merge_across_papers({"a": [0.1] * 40, "b": [0.5] * 40}, budget=60, floor=30)
    assert counts == {"a": 30, "b": 30}


def test_merge_never_exceeds_the_budget():
    counts = merge_across_papers({"a": [0.1] * 100, "b": [0.2] * 100}, budget=60, floor=5)
    assert sum(counts.values()) == 60


def test_merge_never_exceeds_what_a_paper_has():
    counts = merge_across_papers({"a": [0.1, 0.2], "b": [0.3] * 50}, budget=60, floor=10)
    assert counts["a"] == 2


def test_merge_redistributes_an_exhausted_papers_share():
    counts = merge_across_papers({"a": [0.1, 0.2], "b": [0.3] * 50}, budget=20, floor=10)
    assert counts == {"a": 2, "b": 18}


def test_merge_of_a_single_paper_is_a_plain_budget_slice():
    assert merge_across_papers({"a": [0.1] * 90}, budget=60, floor=5) == {"a": 60}


def test_merge_handles_no_papers():
    assert merge_across_papers({}, budget=60, floor=5) == {}


def test_merge_interleaves_when_the_budget_cannot_cover_every_floor():
    """THE fairness lock. With budget < floor * papers and BOTH papers able to
    fill their floor, only an interleaved round-robin splits the shortfall --
    a sequential per-paper fill would hand the first paper everything and
    starve the second, while passing every other test in this file."""
    counts = merge_across_papers(
        {"a": [0.1] * 10, "b": [0.2] * 10},
        budget=6,
        floor=10,
    )
    assert counts == {"a": 3, "b": 3}
