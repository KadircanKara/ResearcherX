from app.services.paper_resolver import (
    ResolvablePaper,
    match_by_author,
    match_by_title_span,
    match_by_year,
    resolve_papers,
)

DEADLY = ResolvablePaper(
    paper_id="p1",
    title="Breaking the Deadly Triad in Offline Reinforcement Learning",
    authors=("Ada Lovelace", "Grace Hopper"),
    year=2023,
)
LAZY = ResolvablePaper(
    paper_id="p2",
    title="Lazy Agents and Credit Assignment in Multi-Agent RL",
    authors=("Evsen Yanmaz",),
    year=2021,
)
SURVEY = ResolvablePaper(
    paper_id="p3",
    title="A Survey of Deep Reinforcement Learning for UAV Swarms",
    authors=("Islam Guven",),
    year=2024,
)
LIBRARY = [DEADLY, LAZY, SURVEY]


def test_matches_a_four_word_contiguous_span():
    matches = match_by_title_span("What does Breaking the Deadly Triad say about Q?", LIBRARY)
    assert [m.paper_ids for m in matches] == [("p1",)]


def test_matches_two_papers_named_in_one_question():
    matches = match_by_title_span(
        "In Breaking the Deadly Triad and Lazy Agents and Credit Assignment, how is reward shaped?",
        LIBRARY,
    )
    assert sorted(pid for m in matches for pid in m.paper_ids) == ["p1", "p2"]


def test_subject_vocabulary_does_not_match():
    """THE load-bearing regression. 'deep reinforcement learning' is this
    domain's own vocabulary and sits inside many titles; a three-word span
    must never resolve a paper."""
    assert match_by_title_span("How does deep reinforcement learning work?", LIBRARY) == []


def test_a_span_matching_two_papers_is_reported_as_ambiguous():
    dup = ResolvablePaper(
        paper_id="p4",
        title="Lazy Agents and Credit Assignment Revisited",
        authors=(),
        year=2025,
    )
    matches = match_by_title_span("What about Lazy Agents and Credit Assignment?", [LAZY, dup])
    assert len(matches) == 1
    assert sorted(matches[0].paper_ids) == ["p2", "p4"]


def test_matching_ignores_case_accents_and_punctuation():
    matches = match_by_title_span("see 'breaking the deadly triad' please", LIBRARY)
    assert [m.paper_ids for m in matches] == [("p1",)]


def test_a_short_full_title_matches_below_the_word_bar():
    short = ResolvablePaper(paper_id="p5", title="Deadly Triad", authors=(), year=2020)
    matches = match_by_title_span("what does Deadly Triad claim?", [short])
    assert [m.paper_ids for m in matches] == [("p5",)]


def test_no_papers_returns_no_matches():
    assert match_by_title_span("anything at all here", []) == []


def test_empty_question_returns_no_matches():
    assert match_by_title_span("", LIBRARY) == []


def test_a_title_span_does_not_match_inside_a_longer_word():
    """THE anti-collision lock. `_contains_run` pads both sides with spaces so
    a title's tokens cannot match inside a larger word. Without the padding a
    bare substring test would resolve this paper, which is the false-positive
    class the whole module is built to prevent."""
    short = ResolvablePaper(
        paper_id="p6",
        title="RL Survey",
        authors=(),
        year=2022,
    )
    assert match_by_title_span("girl survey of methods", [short]) == []


def test_matches_an_author_after_by():
    assert match_by_author("What does the paper by Yanmaz argue?", LIBRARY) == ["p2"]


def test_matches_an_author_in_et_al_form():
    assert match_by_author("Summarise Guven et al.", LIBRARY) == ["p3"]


def test_matches_an_author_in_possessive_form():
    assert match_by_author("What is Hopper's paper about?", LIBRARY) == ["p1"]


def test_matches_authored_by_form():
    assert match_by_author("the one authored by Lovelace", LIBRARY) == ["p1"]


def test_a_surname_colliding_with_a_common_word_does_not_match():
    """THE load-bearing regression. 'How' is a real surname on the live corpus
    and opens a large share of all questions. Only an attribution construction
    may introduce a name, so a bare 'How' can never resolve one."""
    how = ResolvablePaper(
        paper_id="p9", title="Some Unrelated Title", authors=("Ada How",), year=2022
    )
    assert match_by_author("How do these two papers differ?", [how, *LIBRARY]) == []


def test_a_lowercase_name_inside_an_attribution_does_not_match():
    """Capitalisation is checked against the ORIGINAL question: 'by park' is
    ordinary prose, 'by Park' is an attribution."""
    park = ResolvablePaper(paper_id="p8", title="Another Title", authors=("Jae Park",), year=2020)
    assert match_by_author("walking by park benches", [park]) == []
    assert match_by_author("the study by Park", [park]) == ["p8"]


def test_matches_diacritics_folded():
    assert match_by_author(
        "the paper by Yanmaz",
        [ResolvablePaper(paper_id="pX", title="T", authors=("Evşen Yanmaz",), year=2021)],
    ) == ["pX"]


def test_an_author_on_several_papers_returns_all_of_them():
    """The matcher reports every paper the name covers; deciding whether that
    is a resolution or an ambiguity belongs to resolve_papers()."""
    a = ResolvablePaper(paper_id="pa", title="First", authors=("Evsen Yanmaz",), year=2020)
    b = ResolvablePaper(paper_id="pb", title="Second", authors=("Evsen Yanmaz",), year=2021)
    assert match_by_author("papers by Yanmaz", [a, b]) == ["pa", "pb"]


def test_no_attribution_construction_returns_nothing():
    assert match_by_author("Yanmaz swarm coordination results", LIBRARY) == []


def test_the_capitalisation_guard_is_checked_against_the_original_question():
    """Removing the [A-Z] anchor from the attribution regexes must break this.
    'by wang' is ordinary prose ("passed by wang stalls"); 'by Wang' is an
    attribution. Only the original question carries that distinction --
    normalising first would destroy the evidence this guard depends on."""
    wang = ResolvablePaper(paper_id="pw", title="Some Title", authors=("Lei Wang",), year=2020)
    assert match_by_author("we walked by wang market yesterday", [wang]) == []
    assert match_by_author("the method by Wang", [wang]) == ["pw"]


def test_an_over_captured_capital_does_not_match_a_second_paper():
    """THE false-match lock. The {1,3} capture sweeps in "Turing"; only the
    longest-prefix rule stops that from resolving Turing's paper."""
    wang = ResolvablePaper(paper_id="w", title="T", authors=("Lei Wang",), year=2020)
    turing = ResolvablePaper(paper_id="t", title="T", authors=("Alan Turing",), year=1950)
    question = "Summarize the paper by Wang Turing award winners often cite."
    assert match_by_author(question, [wang, turing]) == ["w"]


def test_an_over_captured_noise_word_still_resolves_the_real_name():
    wang = ResolvablePaper(paper_id="w", title="T", authors=("Lei Wang",), year=2020)
    assert match_by_author("by Wang Figure 3 shows a strong trend", [wang]) == ["w"]


def test_a_full_name_span_matches_the_author():
    yan = ResolvablePaper(paper_id="y", title="T", authors=("Evşen Yanmaz",), year=2021)
    assert match_by_author("the paper by Evsen Yanmaz", [yan]) == ["y"]


def test_a_diacritic_initial_surname_is_a_valid_attribution():
    sahin = ResolvablePaper(paper_id="s", title="T", authors=("Şahin Yıldız",), year=2021)
    assert match_by_author("the paper by Şahin", [sahin]) == ["s"]


def test_a_curly_apostrophe_possessive_matches():
    """Built with chr(0x2019), never a literal ': a literal has been flattened
    to ASCII twice in this file's history, which left this test passing while
    exercising nothing."""
    hopper = ResolvablePaper(paper_id="h", title="T", authors=("Grace Hopper",), year=1959)
    question = "What is Hopper" + chr(0x2019) + "s paper about?"
    assert match_by_author(question, [hopper]) == ["h"]


def test_the_attribution_patterns_carry_the_curly_apostrophe():
    """Structural lock. The behavioural test above can only fail if the class
    loses U+2019; this one fails the moment it is flattened, naming the cause
    directly instead of leaving a confusing behavioural miss."""
    from app.services.paper_resolver import _ATTRIBUTION_RES

    assert any(chr(0x2019) in pattern.pattern for pattern in _ATTRIBUTION_RES)


def test_year_matches_a_four_digit_year_in_the_question():
    assert match_by_year("what did the 2021 paper find?", LIBRARY) == ["p2"]


def test_year_matches_every_paper_from_that_year():
    twin = ResolvablePaper(paper_id="p7", title="Other", authors=(), year=2021)
    assert match_by_year("the 2021 work", [LAZY, twin]) == ["p2", "p7"]


def test_year_ignores_numbers_that_are_not_years():
    """Locks the \\b anchors specifically, not just 'no run of exactly 4
    digits' -- the old input ("equation 42 and 199 agents") has no four-digit
    run at all, so it passed even with a stub returning []. Here 'b2021' has
    a word character directly before 2021 (only the LEADING \\b blocks it);
    '20211' has an extra digit directly after 2021 (only the TRAILING \\b
    blocks it); 'model2021x' has both. Confirmed by hand: stripping the
    leading \\b alone from _YEAR_RE lets 'b2021' match (2021 is LAZY's year,
    so match_by_year would return ["p2"]); stripping the trailing \\b alone
    lets '20211' match the same way."""
    question = "b2021 report and 20211 booked and model2021x version"
    assert match_by_year(question, LIBRARY) == []


def test_resolve_returns_both_titled_papers():
    ids = resolve_papers(
        "In Breaking the Deadly Triad and Lazy Agents and Credit Assignment, compare rewards.",
        LIBRARY,
        max_papers=5,
    )
    assert sorted(ids) == ["p1", "p2"]


def test_resolve_falls_through_when_any_span_is_ambiguous():
    dup = ResolvablePaper(
        paper_id="p4", title="Lazy Agents and Credit Assignment Revisited", authors=(), year=2025
    )
    assert (
        resolve_papers("about Lazy Agents and Credit Assignment", [LAZY, dup], max_papers=5) == []
    )


def test_resolve_uses_author_when_no_title_span():
    assert resolve_papers("what does the paper by Yanmaz claim?", LIBRARY, max_papers=5) == ["p2"]


def test_resolve_falls_through_when_an_author_covers_several_papers():
    a = ResolvablePaper(paper_id="pa", title="First", authors=("Evsen Yanmaz",), year=2020)
    b = ResolvablePaper(paper_id="pb", title="Second", authors=("Evsen Yanmaz",), year=2021)
    assert resolve_papers("the paper by Yanmaz", [a, b], max_papers=5) == []


def test_resolve_narrows_an_ambiguous_author_by_year():
    a = ResolvablePaper(paper_id="pa", title="First", authors=("Evsen Yanmaz",), year=2020)
    b = ResolvablePaper(paper_id="pb", title="Second", authors=("Evsen Yanmaz",), year=2021)
    assert resolve_papers("the 2021 paper by Yanmaz", [a, b], max_papers=5) == ["pb"]


def test_resolve_uses_a_year_alone_only_when_unique():
    twin = ResolvablePaper(paper_id="p7", title="Other", authors=(), year=2021)
    assert resolve_papers("the 2024 paper", LIBRARY, max_papers=5) == ["p3"]
    assert resolve_papers("the 2021 paper", [LAZY, twin], max_papers=5) == []


def test_resolve_returns_nothing_for_a_single_paper_project():
    assert resolve_papers("Breaking the Deadly Triad, what is it?", [DEADLY], max_papers=5) == []


def test_resolve_falls_through_when_the_set_exceeds_the_cap():
    """Exercises the cap through TITLE SPANS -- three papers are each named
    outright, which is an unambiguous resolution of three, and max_papers=2
    must discard the whole result rather than answer about an arbitrary two
    of the three the user named."""
    named = [
        ResolvablePaper(
            paper_id="m1", title="Swarm Coordination Under Jamming", authors=(), year=2019
        ),
        ResolvablePaper(
            paper_id="m2", title="Energy Aware Path Planning Revisited", authors=(), year=2020
        ),
        ResolvablePaper(
            paper_id="m3", title="Fair Comparison Of Fleet Policies", authors=(), year=2021
        ),
    ]
    question = (
        "Compare Swarm Coordination Under Jamming, Energy Aware Path Planning "
        "Revisited and Fair Comparison Of Fleet Policies."
    )
    assert sorted(resolve_papers(question, named, max_papers=5)) == ["m1", "m2", "m3"]
    assert resolve_papers(question, named, max_papers=2) == []


def test_resolve_returns_nothing_for_a_general_question():
    assert resolve_papers("what is reinforcement learning?", LIBRARY, max_papers=5) == []


def test_resolve_falls_through_when_an_unambiguous_span_accompanies_an_ambiguous_one():
    """THE lock on 'fall through ENTIRELY'. With only one span in play the
    existing ambiguity test passes against a mutant that keeps the unambiguous
    spans and drops only the ambiguous one; this one fails against it."""
    dup = ResolvablePaper(
        paper_id="p4",
        title="Lazy Agents and Credit Assignment Revisited",
        authors=(),
        year=2025,
    )
    question = "Compare Breaking the Deadly Triad with Lazy Agents and Credit Assignment."
    assert resolve_papers(question, [DEADLY, LAZY, dup], max_papers=5) == []


def test_resolve_unions_a_titled_paper_with_a_single_attributed_author():
    question = "Compare Breaking the Deadly Triad and the paper by Yanmaz."
    assert sorted(resolve_papers(question, LIBRARY, max_papers=5)) == ["p1", "p2"]


def test_resolve_keeps_titles_alone_when_the_attributed_author_is_ambiguous():
    a = ResolvablePaper(
        paper_id="pa", title="First Paper Title Here", authors=("Evsen Yanmaz",), year=2020
    )
    b = ResolvablePaper(
        paper_id="pb", title="Second Paper Title Here", authors=("Evsen Yanmaz",), year=2021
    )
    question = "Compare Breaking the Deadly Triad and the paper by Yanmaz."
    assert resolve_papers(question, [DEADLY, a, b], max_papers=5) == ["p1"]


def test_resolve_falls_through_when_a_named_year_appears_in_a_title():
    """The measured false hit: without the corroboration check this resolves
    b2, the paper the user did not mean."""
    bench = ResolvablePaper(
        paper_id="b1", title="The 2019 Benchmark For UAV Swarms", authors=(), year=2022
    )
    other = ResolvablePaper(
        paper_id="b2", title="Totally Unrelated Fleet Study", authors=(), year=2019
    )
    assert (
        resolve_papers("what does the 2019 benchmark paper find?", [bench, other], max_papers=5)
        == []
    )
