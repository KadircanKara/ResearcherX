"""Claim extraction. Pure — no DB, no LLM.

Every number the groundedness harness reports is denominated in what
`extract_claims` returns, so these pin the segmentation policy itself.
"""

from evals.groundedness.claims import extract_claims, marker_count


def test_each_sentence_becomes_its_own_claim():
    answer = (
        "The system uses probabilistic occupancy updates [1]. "
        "Revisit time is minimised across the mission area [2]."
    )
    claims = extract_claims(answer)
    assert [c.index for c in claims] == [0, 1]
    assert claims[0].markers == (1,)
    assert claims[1].markers == (2,)


def test_a_marker_after_the_terminator_belongs_to_the_sentence_it_follows():
    """The model writes '... in the simulation. [4]' about as often as it puts
    the marker inside. Attaching that marker to the NEXT sentence would score
    the wrong claim's provenance."""
    claims = extract_claims("Coverage improves by 12%. [4] A second effect is redundancy [5].")
    assert claims[0].markers == (4,)
    assert claims[1].markers == (5,)


def test_code_is_never_a_claim_and_its_brackets_are_never_markers():
    """A fenced block has no truth value, and `arr[4]` inside one is an index.
    The guard is shared with the production citation passes via
    `citation_attribution.split_prose_segments` — never re-implemented here."""
    answer = (
        "The reward is shaped as follows [2].\n\n"
        "```python\n"
        "reward = arr[4] + arr[7]\n"
        "```\n\n"
        "This penalises collisions [3]."
    )
    claims = extract_claims(answer)
    assert len(claims) == 2
    assert claims[0].markers == (2,)
    assert claims[1].markers == (3,)
    assert marker_count(answer) == 2


def test_an_abbreviation_does_not_end_a_sentence():
    """'et al.' splitting would leave two fragments that are each unjudgeable."""
    claims = extract_claims(
        "The approach of Yanmaz et al. is a multi-objective formulation of the problem [1]."
    )
    assert len(claims) == 1
    assert claims[0].markers == (1,)


def test_fragments_shorter_than_a_claim_are_dropped_but_still_counted_as_markers():
    """A stray list remnant carries no assertion, so judging it wastes a call
    and pollutes the denominator — but the reader still saw its marker, and
    `marker_count` is what makes that gap visible instead of silent."""
    answer = "Yes [9]. The swarm maintains connectivity through a relay chain [4]."
    claims = extract_claims(answer)
    assert len(claims) == 1
    assert claims[0].markers == (4,)
    assert marker_count(answer) == 2


def test_list_bullets_keep_their_markup_and_are_judged_individually():
    answer = (
        "Benefits:\n"
        "- It improves reliability of sensed data [1].\n"
        "- It enables timely updates in dynamic missions [2].\n"
    )
    claims = extract_claims(answer)
    assert len(claims) == 2
    assert claims[0].markers == (1,)
    assert claims[1].markers == (2,)


def test_an_answer_with_no_markers_still_yields_claims():
    """The measured failure mode: the model writes uncited bullets and dumps
    every marker in one trailing sentence. Those bullets must still be judged
    — that is what `citation_coverage` counts."""
    claims = extract_claims(
        "It improves the reliability of sensed data by compensating for imperfect sensors. "
        "It creates redundancy that also benefits network connectivity."
    )
    assert len(claims) == 2
    assert all(c.markers == () for c in claims)


def test_claims_after_the_prompts_own_hand_off_are_marked_disclosed():
    """Production's SYSTEM prompt instructs exactly this: decline, then answer
    from general knowledge. Everything after the hand-off is unsupported by
    construction, so scoring it as hallucination would report the prompt
    working as designed as a defect."""
    answer = (
        "The assigned papers do not appear to cover this. "
        "Based on general knowledge: FPV goggles include the Fat Shark Dominator series. "
        "Video transmitters should have adjustable power output."
    )
    claims = extract_claims(answer)
    # The disclaiming sentence is itself a checkable claim ABOUT the corpus.
    assert claims[0].disclosed is False
    assert all(c.disclosed for c in claims[1:])


def test_a_hand_off_that_shares_its_sentence_with_the_content_is_disclosed():
    """The model writes it this way whenever the disclaimer ends in a colon.
    Measured on the reference run: offtopic-spray-nozzle scored as an
    undisclosed hallucination purely because of that colon."""
    claims = extract_claims(
        "Based on general knowledge: nozzle size and boom pressure depend on droplet "
        "size, flight speed and canopy density."
    )
    assert claims[0].disclosed is True


def test_a_bare_mention_of_general_knowledge_does_not_disclose_the_whole_answer():
    """A hand-off announced but not taken leaves the following claims checkable
    — otherwise one stray phrase would exempt an entire grounded answer."""
    claims = extract_claims(
        "The papers describe a multi-objective formulation [1]. "
        "The revisit time constraint improves update frequency [2]."
    )
    assert not any(c.disclosed for c in claims)
