"""The header on a chunk: composed in one place, embedded without the page,
shown with it.

Pure. Index time and read time both call these, so the vector and the
excerpt cannot drift apart.
"""

from app.services.chunk_header import embed_text, excerpt_text, section_path_text

TITLE = "Cooperative Multi-Target Search with UAV Swarms"
SECTION = ("IV. RL-BASED PLANNER", "B. Reward Function")
TEXT = "The reward is a weighted sum designed to promote cooperative search."


def test_section_path_joins_with_a_chevron():
    assert section_path_text(SECTION) == "IV. RL-BASED PLANNER > B. Reward Function"
    assert section_path_text(()) == ""


def test_embed_text_carries_title_and_section_then_the_text():
    out = embed_text(TITLE, SECTION, TEXT)
    assert out.startswith(
        "[Title: " + TITLE + " | Section: IV. RL-BASED PLANNER > B. Reward Function]"
    )
    assert out.endswith("\n\n" + TEXT)


def test_embed_text_never_contains_a_page():
    """A page number is a locator, not meaning; a bare numeral in the vector
    only ever matches other numerals."""
    assert "Page" not in embed_text(TITLE, SECTION, TEXT)


def test_empty_section_emits_no_section_field():
    out = embed_text(TITLE, (), TEXT)
    assert out.startswith("[Title: " + TITLE + "]\n\n")
    assert "Section" not in out


def test_excerpt_text_adds_the_page_when_known():
    assert "| Page: 3]" in excerpt_text(TITLE, SECTION, 3, TEXT)
    assert "Page" not in excerpt_text(TITLE, SECTION, None, TEXT)


def test_excerpt_and_embed_share_the_header_prefix():
    e = embed_text(TITLE, SECTION, TEXT)
    x = excerpt_text(TITLE, SECTION, None, TEXT)
    assert e == x


def test_the_text_itself_is_never_modified():
    weird = "  leading spaces and [brackets] and | pipes  "
    assert embed_text(TITLE, (), weird).endswith("\n\n" + weird)
