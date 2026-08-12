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
