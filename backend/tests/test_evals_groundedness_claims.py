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


def test_inline_code_stays_inside_its_sentence():
    """gpt-5-mini writes variables in backticks. Cutting the prose at each span
    turned grounded sentences into judge-proof fragments ("), so the current
    cell probability at time") -- 12 of the 13 "unsupported" claims on the
    2026-09-23 gpt-5-mini run."""
    answer = (
        "Probabilities are updated with a Bayesian rule (sensor parameters `p` and `q`), "
        "so the cell probability at time `t` depends on the prior at `t-1` [1]. "
        "The mission ends once confidence exceeds `B` [2]."
    )
    claims = extract_claims(answer)
    assert [c.text for c in claims] == [
        "Probabilities are updated with a Bayesian rule (sensor parameters `p` and `q`), "
        "so the cell probability at time `t` depends on the prior at `t-1` [1].",
        "The mission ends once confidence exceeds `B` [2].",
    ]
    assert [c.markers for c in claims] == [(1,), (2,)]


def test_a_bracket_inside_inline_code_is_not_a_marker_and_cannot_end_a_sentence():
    claims = extract_claims("The reward reads `arr[4]. next` from the buffer each step [3].")
    assert len(claims) == 1
    assert claims[0].text == "The reward reads `arr[4]. next` from the buffer each step [3]."
    assert claims[0].markers == (3,)


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


def test_the_production_refusal_is_not_a_claim():
    """It is a control response, not an assertion about a paper. Left in, it
    was graded -- and the two judges measured on 2026-08-22 disagreed about it
    on every negative case, which is harness noise, not model behaviour."""
    claims = extract_claims("The ingested documents do not cover this.")
    assert claims == []


def test_a_refusal_does_not_swallow_the_rest_of_an_answer():
    """Only the refusal sentence is dropped; anything else in the reply is
    still judged, so a model that refuses and then keeps talking is caught."""
    claims = extract_claims(
        "The ingested documents do not cover this. "
        "However, swarm coordination generally relies on consensus protocols."
    )
    assert len(claims) == 1
    assert "consensus protocols" in claims[0].text


def test_a_marker_ending_the_answer_stays_with_its_sentence():
    """It used to split off as a bare "[2]" and drop as a fragment, so the
    last claim of most answers read as uncited."""
    claims = extract_claims("Each episode consists of 505 tasks and 10 robots. [2]")
    assert len(claims) == 1
    assert claims[0].markers == (2,)
    assert claims[0].text.endswith("[2]")


def test_several_markers_ending_the_answer_all_stay():
    claims = extract_claims("Each episode consists of 505 tasks and 10 robots. [2] [3]\n")
    assert claims[0].markers == (2, 3)


def test_a_sentence_ending_the_answer_with_no_marker_is_unchanged():
    claims = extract_claims("Each episode consists of 505 tasks and 10 robots.")
    assert [c.text for c in claims] == ["Each episode consists of 505 tasks and 10 robots."]


def test_marker_only_bullets_belong_to_the_text_above_them():
    answer = (
        "- The third layer is dedicated to auto flight control.\n"
        "  - [1]\n"
        "  - [2]\n\n"
        "- Flight commands are issued through the vendor SDK [3].\n"
    )
    claims = extract_claims(answer)
    assert [c.markers for c in claims] == [(1, 2), (3,)]
    assert claims[1].text == "- Flight commands are issued through the vendor SDK [3]."


def test_a_bare_marker_line_belongs_to_the_text_above_it():
    claims = extract_claims(
        "The planner minimises revisit time.\n[4]\n\nIt runs at 10 Hz on board."
    )
    assert [c.markers for c in claims] == [(4,), ()]


def test_a_marker_ending_a_real_list_item_is_left_where_it_is():
    claims = extract_claims(
        "- The planner minimises revisit time [1].\n- It runs at 10 Hz on board [2]."
    )
    assert [c.markers for c in claims] == [(1,), (2,)]


def test_a_closing_offer_is_not_a_claim():
    answer = (
        "The planner minimises revisit time [1].\n\n"
        "If you want, I can extract the specific equations from the paper."
    )
    assert [c.markers for c in extract_claims(answer)] == [(1,)]


def test_an_offer_carrying_markers_is_still_not_a_claim():
    answer = (
        "The planner minimises revisit time [1].\n\n"
        "If you’d like, I can extract the entries of Table 2 [1] [2]."
    )
    assert len(extract_claims(answer)) == 1


def test_every_item_of_an_offered_list_goes_with_the_offer():
    answer = (
        "The planner minimises revisit time [1].\n\n"
        "If you want, I can:\n\n"
        "- show the linear program as stated in the paper.\n"
        "- produce a small numeric example with two robots.\n"
    )
    assert len(extract_claims(answer)) == 1


def test_a_parenthesised_offer_is_not_a_claim():
    answer = (
        "The planner minimises revisit time [1].\n\n"
        "(If you want specific parameter examples, tell me which paper to use.)"
    )
    assert len(extract_claims(answer)) == 1


def test_a_request_to_be_told_counts_as_an_offer():
    answer = (
        "The planner minimises revisit time [1].\n\n"
        "If you want the raw logged metrics, tell me which one and I will extract it."
    )
    assert len(extract_claims(answer)) == 1


def test_a_recommendation_opening_with_the_same_words_is_a_claim():
    """Measured on a real answer: this is the sentence an unanchored rule eats."""
    answer = "If you need fast, near-complete coverage, centralized planning is preferable [8]."
    assert [c.markers for c in extract_claims(answer)] == [(8,)]


def test_an_offer_followed_by_more_content_was_not_closing_the_answer():
    """Stripping too much hides real claims, so this falls through to the judge."""
    answer = (
        "If you want, I can extract the specific equations from the paper.\n\n"
        "The planner minimises revisit time [1]."
    )
    assert len(extract_claims(answer)) == 2


def test_an_offer_before_a_fenced_block_leaves_the_prose_after_it_judged():
    answer = (
        "Let me know if you want more detail on this topic.\n\n"
        "```python\nx = 1\n```\n\n"
        "The planner minimises revisit time [1]."
    )
    assert len(extract_claims(answer)) == 2


def test_markers_inside_a_closing_offer_are_still_counted_for_the_reader():
    answer = "The planner minimises revisit time [1].\n\nIf you want, I can show Table 2 [2]."
    assert marker_count(answer) == 2
