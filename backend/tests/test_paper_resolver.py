from app.services.paper_resolver import (
    ResolvablePaper,
    match_by_author,
    match_by_title_span,
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
    hopper = ResolvablePaper(paper_id="h", title="T", authors=("Grace Hopper",), year=1959)
    assert match_by_author("What is Hopper's paper about?", [hopper]) == ["h"]
