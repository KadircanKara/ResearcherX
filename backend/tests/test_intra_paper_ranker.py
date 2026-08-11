"""keep_within_paper: the entire intra-paper selection policy, in isolation.

The function is pure so both production (chat_service) and the eval harness
can call the SAME policy — a re-implementation on either side would drift
silently, and the whole point of the cut is that it is measurable.
"""

from app.services.intra_paper_ranker import keep_within_paper


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
