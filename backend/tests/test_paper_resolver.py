from app.services.paper_resolver import (
    ResolvablePaper,
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
