"""Heading levels, the level stack, and outline-marker injection.

Pure, like mention_ranker: the chunker and the eval harness need the same
answer to "what section is this text in", so the rule lives in one place.
"""

from app.services.section_outline import (
    MARKER_RE,
    Anchor,
    SectionStack,
    clean_heading,
    format_marker,
    heading_level,
    inject_markers,
)


# ── heuristic ────────────────────────────────────────────────────────────


def test_roman_numeral_prefix_is_level_one():
    assert heading_level("IV. RL-BASED PLANNER", 2, False) == 1
    assert heading_level("iv) Results", 2, False) == 1


def test_italic_or_bold_letter_prefix_is_level_two():
    assert heading_level("_B. Reward Function_", 2, False) == 2
    assert heading_level("**C. Training Setup**", 2, False) == 2


def test_dotted_decimal_prefix_counts_its_components():
    assert heading_level("3.2 Multi-UAV coordination", 2, False) == 2
    assert heading_level("**1. Introduction**", 2, False) == 1
    assert heading_level("4.1.3 Edge case", 2, False) == 3


def test_markdown_depth_breaks_ties_only_when_it_varies():
    """All-`##` documents carry no depth information; depth is used only
    when the paper actually mixes levels."""
    assert heading_level("Related work", 3, True) == 3
    assert heading_level("Related work", 3, False) == 1


def test_unrecognised_heading_is_flat_level_one():
    assert heading_level("ACKNOWLEDGMENTS", 2, False) == 1
    assert heading_level("References", 2, False) == 1


def test_clean_heading_strips_markdown_decoration():
    assert clean_heading("_B. Reward Function_ ") == "B. Reward Function"
    assert clean_heading("**1. Introduction**") == "1. Introduction"
    assert clean_heading("`code`") == "code"


# ── stack ────────────────────────────────────────────────────────────────


def test_stack_nests_level_two_under_level_one_and_resets_on_next_level_one():
    s = SectionStack()
    assert s.push(1, "II. SYSTEM MODEL") == ("II. SYSTEM MODEL",)
    assert s.push(2, "A. Mission") == ("II. SYSTEM MODEL", "A. Mission")
    assert s.push(2, "B. Sensing") == ("II. SYSTEM MODEL", "B. Sensing")
    assert s.push(1, "III. EA PLANNER") == ("III. EA PLANNER",)


def test_stack_starts_empty_for_front_matter():
    assert SectionStack().path == ()


def test_a_level_two_with_no_parent_is_a_one_element_path():
    """Never invent a parent. A subsection before any section is exactly
    what it looks like: the only thing known about the text above it."""
    s = SectionStack()
    assert s.push(2, "A. Orphan") == ("A. Orphan",)


# ── markers ──────────────────────────────────────────────────────────────


def test_marker_round_trips_a_title_with_quotes():
    m = format_marker(2, 'B. "Reward" Function')
    match = MARKER_RE.search(m)
    assert match is not None
    assert int(match.group("level")) == 2
    import json

    assert json.loads(match.group("title")) == 'B. "Reward" Function'


def test_marker_regex_does_not_match_ordinary_html_comments():
    assert MARKER_RE.search("<!-- just a note -->") is None


# ── injection ────────────────────────────────────────────────────────────

PAGE = (
    "Some intro text about the mission.\n\n"
    "**IV. RL-BASED PLANNER**\n\n"
    "The planner learns a policy.\n\n"
    "_B. Reward Function_ The reward is a weighted sum.\n\n"
    "Then training details.\n"
)


def test_anchor_is_found_through_markdown_decoration():
    """The block's leading words come from PyMuPDF plain text; the markdown
    wraps them in `**` or `_`. The match must see through both."""
    anchors = [
        Anchor(
            page=1,
            level=1,
            title="RL-Based Planner",
            leading_words=("IV.", "RL-BASED", "PLANNER"),
            block_index=1,
        ),
        Anchor(
            page=1,
            level=2,
            title="Reward Function",
            leading_words=("B.", "Reward", "Function", "The", "reward"),
            block_index=3,
        ),
    ]
    pages, report = inject_markers([PAGE], anchors)
    out = pages[0]
    assert report.found == 2 and report.dropped == 0
    i1 = out.index(format_marker(1, "RL-Based Planner"))
    i2 = out.index(format_marker(2, "Reward Function"))
    assert i1 < out.index("**IV. RL-BASED PLANNER**")
    assert i2 < out.index("_B. Reward Function_")
    assert i1 < i2


def test_unfindable_anchor_falls_back_to_paragraph_position():
    anchors = [
        Anchor(
            page=1,
            level=1,
            title="Ghost",
            leading_words=("text", "absent", "from", "markdown"),
            block_index=2,
        )
    ]
    pages, report = inject_markers([PAGE], anchors)
    assert report.by_paragraph == 1
    paras = pages[0].split("\n\n")
    assert paras[2].startswith(format_marker(1, "Ghost"))


def test_anchor_beyond_the_page_is_dropped_not_guessed():
    anchors = [Anchor(page=1, level=1, title="Ghost", leading_words=("nothing",), block_index=99)]
    pages, report = inject_markers([PAGE], anchors)
    assert report.dropped == 1
    assert "Ghost" not in pages[0]


def test_injection_leaves_other_pages_untouched():
    anchors = [
        Anchor(page=2, level=1, title="X", leading_words=("Then", "training"), block_index=0)
    ]
    pages, _ = inject_markers([PAGE, "Then training details."], anchors)
    assert pages[0] == PAGE
    assert pages[1].startswith(format_marker(1, "X"))
