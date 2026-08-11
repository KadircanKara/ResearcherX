"""needs_paper_metadata — pure keyword routing, no service or network involved."""

import pytest

from app.db.models import Paper
from app.services.chat_service import _METADATA_KEYWORDS, needs_paper_metadata


@pytest.mark.parametrize("word", _METADATA_KEYWORDS)
def test_every_keyword_fires(word: str):
    assert needs_paper_metadata(f"tell me the {word} please", [], []) is True


def test_a_plain_content_question_does_not_fire():
    assert needs_paper_metadata("What reward function does the planner use?", [], []) is False


def test_matching_is_case_insensitive():
    assert needs_paper_metadata("WHO wrote this?", [], []) is True


@pytest.mark.parametrize(
    "question",
    [
        "who are the authors?",
        "list the citations",
        "when was it published?",
        "what years were covered?",
        "which conferences?",
    ],
)
def test_plural_and_inflected_forms_fire(question: str):
    """The case a trailing word boundary would silently break, and the reason
    matching is anchored at word start only: \\bauthor\\b misses "authors",
    \\bcite\\b misses "citations", \\bpublish\\b misses "published". Each of
    those is a real metadata question falling into the false-negative case,
    where the model reports that a paper does not state its authors."""
    assert needs_paper_metadata(question, [], []) is True


def test_a_keyword_inside_another_word_does_not_fire():
    # "update" contains "date", but not at a word start.
    assert needs_paper_metadata("update the search parameters", [], []) is False


def test_a_pronoun_follow_up_after_a_metadata_question_fires():
    """Keywords cannot rescue "and that one?" — nothing in it is about
    metadata. The previous user turn is what carries the intent."""
    prior = [
        {"role": "user", "content": "Who wrote the UAV swarm paper?"},
        {"role": "assistant", "content": "Kara and Yanmaz."},
    ]
    assert needs_paper_metadata("And that one?", prior, []) is True


def test_the_carry_back_is_one_turn_only():
    """Two turns later the intent has lapsed. Widening the window buys a
    shrinking set of real cases for a growing number of false positives."""
    prior = [
        {"role": "user", "content": "Who wrote the UAV swarm paper?"},
        {"role": "assistant", "content": "Kara and Yanmaz."},
        {"role": "user", "content": "What reward function does it use?"},
        {"role": "assistant", "content": "A weighted sum."},
    ]
    assert needs_paper_metadata("And that one?", prior, []) is False


def test_an_empty_history_does_not_raise():
    assert needs_paper_metadata("And that one?", [], []) is False


def test_assistant_turns_are_ignored_when_carrying_back():
    """Only what the USER asked carries intent. An assistant answer mentioning
    an author must not keep the full block alive on the next turn."""
    prior = [
        {"role": "user", "content": "What reward function does it use?"},
        {"role": "assistant", "content": "The authors describe a weighted sum."},
    ]
    assert needs_paper_metadata("And that one?", prior, []) is False


def test_a_history_entry_missing_content_does_not_raise():
    """Not reachable today — every construction site in chat_service.py
    supplies both keys — but this sits on the hot path of every chat turn,
    and a KeyError escaping here would fail the whole turn to satisfy a
    token optimisation. The most recent user entry is treated as
    content-free rather than fatal."""
    prior = [{"role": "user"}]
    assert needs_paper_metadata("And that one?", prior, []) is False


@pytest.mark.parametrize("word", ["citing", "dating"])
def test_truncated_stems_cover_the_ing_inflection(word: str):
    """ "cite" and "date" are truncated to "cit" and "dat" in
    _METADATA_KEYWORDS because the whole words miss this inflection: the
    vowel changes right after the stem ("cite" vs "citing", "date" vs
    "dating"), so no trailing-boundary fix could close the gap — only a
    shorter stem does."""
    assert needs_paper_metadata(f"tell me the {word} please", [], []) is True


def test_recently_fires_via_the_recent_stem():
    """ "recent" is anchored at word start only, like every other stem, so
    the same over-firing trade lets the common inflection "recently" fire
    too, without a dedicated keyword of its own."""
    assert needs_paper_metadata("Was this one added recently?", [], []) is True


# --- Corpus-derived tokens: surnames, venue words, bare years -------------
#
# No English keyword list can catch a question that REFERENCES a paper's
# metadata instead of asking for it by name — the trigger tokens are corpus
# data (a surname, a bare year, a venue acronym), not vocabulary. Answering
# these wrong ships a titles-only block, and the chat prompt then treats a
# missing field as "the paper does not state it": a confident falsehood.


def test_a_question_naming_an_author_surname_fires():
    papers = [Paper(authors=["Ismail Kara", "Elif Yanmaz"], venue="IEEE ICRA")]
    fires = needs_paper_metadata("What does Kara's paper say about coverage?", [], papers)
    assert fires is True


def test_a_bare_year_fires():
    assert needs_paper_metadata("Summarize the 2023 paper", [], []) is True


def test_a_question_naming_two_author_surnames_fires():
    papers = [
        Paper(authors=["John Smith"], venue=None),
        Paper(authors=["Grace Lee"], venue=None),
    ]
    assert needs_paper_metadata("Compare the Smith and Lee papers", [], papers) is True


def test_a_superlative_fires():
    assert needs_paper_metadata("Which paper is the newest?", [], []) is True


def test_a_surname_shorter_than_three_characters_does_not_fire():
    """Excludes bare initials and particles ("J.", "de", "van") from firing
    on their own — the corpus token set keeps only surnames of length >= 3."""
    papers = [Paper(authors=["Wei Li"], venue=None)]
    assert needs_paper_metadata("What does Li's paper conclude?", [], papers) is False


def test_a_question_matching_no_corpus_token_does_not_fire():
    """Supplying papers must not, by itself, change the answer for a
    question that shares no word with any of them."""
    papers = [Paper(authors=["Jane Kara"], venue="IEEE ICRA")]
    fires = needs_paper_metadata("What reward function does the planner use?", [], papers)
    assert fires is False


def test_a_lowercase_venue_word_does_not_fire():
    """Word-y venue tokens are indistinguishable from the corpus's own
    subject matter -- "robotics" is exactly what a real content question
    asks about -- so only tokens uppercase in the ORIGINAL venue string
    (acronyms) are kept. Measured on the live 100-paper corpus: a
    length-only filter yielded 100 such tokens and made the detector fire
    on nearly every turn, erasing the saving it exists to produce."""
    papers = [Paper(authors=["A B"], venue="international conference on robotics")]
    fires = needs_paper_metadata("What does the robotics approach do?", [], papers)
    assert fires is False


def test_an_uppercase_venue_acronym_fires():
    """The only part of a venue string NOT also the corpus's own content
    vocabulary is its acronym -- exactly what a user types to reference a
    venue ("the ICRA paper")."""
    papers = [Paper(authors=["A B"], venue="IEEE ICRA")]
    assert needs_paper_metadata("What is in the ICRA paper?", [], papers) is True
