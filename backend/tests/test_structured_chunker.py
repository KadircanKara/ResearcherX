"""Section-bounded chunking. The invariant under test: a heading is never
inside a chunk, and every chunk knows its section and its page.

Pure. `RecursiveCharacterTextSplitter` is real (no network); the caption
regex is the production one, imported.
"""

import re

from app.services.paper_ingest_service import _FIGURE_CAPTION_RE
from app.services.section_outline import format_marker
from app.services.structured_chunker import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    ChunkRecord,
    PageText,
    chunk_markdown,
    chunk_pages,
    join_pages,
    page_at,
    segments_from_text,
    split_segments,
)

LONG = "Sentence number %d of the reward section, describing one more component. " * 1


def _para(n: int, seed: str = "Body sentence %d. ") -> str:
    return "".join(seed % i for i in range(n))


MD = (
    "# Cooperative Search\n\n"
    "Front matter abstract text.\n\n"
    "## IV. RL-BASED PLANNER\n\n"
    + _para(8)
    + "\n\n## _A. Observation Space_\n\n"
    + _para(6)
    + "\n\n## _B. Reward Function_\n\n"
    "The reward is a weighted sum designed to promote cooperative search. "
    + _para(4)
    + "\n\nFigure 3. Episode reward over training steps for both planners.\n"
)


# ── pages and offsets ────────────────────────────────────────────────────


def test_join_pages_records_each_page_start():
    doc, starts = join_pages([PageText(1, "aaa"), PageText(2, "bbb"), PageText(3, "c")])
    assert doc == "aaa\n\nbbb\n\nc"
    assert starts == [(0, 1), (5, 2), (10, 3)]
    assert page_at(starts, 0) == 1
    assert page_at(starts, 6) == 2
    assert page_at(starts, 10) == 3


def test_page_at_is_none_when_pages_are_unknown():
    _, starts = join_pages([PageText(None, "x")])
    assert page_at(starts, 0) is None


# ── segmentation from headings (tiers 2 and 3) ───────────────────────────


def test_headings_cut_segments_and_carry_the_path():
    segs = segments_from_text(MD, use_markers=False)
    paths = [s.section for s in segs]
    # MD opens with "# Cooperative Search" (no text above it), so the first
    # segment is the abstract text sitting under that title heading, not root.
    assert paths[0] == ("Cooperative Search",)
    assert ("Cooperative Search",) in paths
    assert ("IV. RL-BASED PLANNER",) in paths
    assert ("IV. RL-BASED PLANNER", "B. Reward Function") in paths


def test_a_heading_line_is_never_inside_a_segment_body():
    for s in segments_from_text(MD, use_markers=False):
        assert not re.search(r"^#{1,4} ", s.text, re.M), s.text[:60]


def test_the_answer_sentence_sits_under_its_own_subsection():
    segs = segments_from_text(MD, use_markers=False)
    hit = [s for s in segs if "weighted sum designed to promote" in s.text]
    assert len(hit) == 1
    assert hit[0].section == ("IV. RL-BASED PLANNER", "B. Reward Function")


# ── segmentation from markers (tier 1) ───────────────────────────────────


def test_markers_cut_segments_and_headings_are_then_ignored():
    doc = (
        "intro\n\n" + format_marker(1, "RL-Based Planner") + "\n"
        "## some heading pymupdf emitted\n\n"
        "planner text\n\n" + format_marker(2, "Reward Function") + "\n"
        "The reward is a weighted sum.\n"
    )
    segs = segments_from_text(doc, use_markers=True)
    assert [s.section for s in segs] == [
        (),
        ("RL-Based Planner",),
        ("RL-Based Planner", "Reward Function"),
    ]
    assert "## some heading" in segs[1].text  # a heading is just text under markers
    assert not any("<!--section" in s.text for s in segs)


# ── splitting ────────────────────────────────────────────────────────────


def test_a_section_shorter_than_chunk_size_is_one_chunk():
    seg = segments_from_text(MD, use_markers=False)
    _, starts = join_pages([PageText(None, MD)])
    recs = split_segments(seg, starts)
    reward = [r for r in recs if r.section == ("IV. RL-BASED PLANNER", "B. Reward Function")]
    assert len(reward) == 1
    assert "weighted sum designed to promote" in reward[0].text


def test_a_long_section_splits_but_every_piece_keeps_the_path_and_overlap_stays_inside():
    long_md = "## I. LONG\n\n" + _para(150) + "\n\n## II. NEXT\n\nshort.\n"
    segs = segments_from_text(long_md, use_markers=False)
    _, starts = join_pages([PageText(None, long_md)])
    recs = split_segments(segs, starts)
    long_recs = [r for r in recs if r.section == ("I. LONG",)]
    assert len(long_recs) >= 2
    assert all(len(r.text) <= CHUNK_SIZE for r in long_recs)
    assert not any("short." in r.text for r in long_recs)  # overlap never crosses the heading
    assert CHUNK_OVERLAP < CHUNK_SIZE


def test_each_chunk_records_the_page_of_its_first_character():
    p1 = "## I. A\n\n" + _para(40)
    p2 = _para(40) + "\n\n## II. B\n\nend."
    pages = [PageText(1, p1), PageText(2, p2)]
    doc, starts = join_pages(pages)
    recs = split_segments(segments_from_text(doc, use_markers=False), starts)
    assert recs[0].page == 1
    assert [r for r in recs if r.section == ("II. B",)][0].page == 2
    pages_seen = {r.page for r in recs if r.section == ("I. A",)}
    assert pages_seen <= {1, 2} and 1 in pages_seen


# ── captions and the whole pipeline ──────────────────────────────────────


def test_chunk_pages_appends_captions_with_their_section_and_page():
    recs = chunk_pages([PageText(3, MD)], use_markers=False, caption_re=_FIGURE_CAPTION_RE)
    caps = [r for r in recs if r.kind == "caption"]
    assert len(caps) == 1
    assert caps[0].text.startswith("Figure 3.")
    assert caps[0].page == 3
    assert caps[0].section == ("IV. RL-BASED PLANNER", "B. Reward Function")
    assert recs.index(caps[0]) == len(recs) - 1  # after all body chunks, as today


def test_chunk_markdown_is_tier_three_with_no_pages():
    recs = chunk_markdown(MD, caption_re=_FIGURE_CAPTION_RE)
    assert all(r.page is None for r in recs)
    assert any(r.section == ("IV. RL-BASED PLANNER", "B. Reward Function") for r in recs)


def test_empty_input_yields_nothing():
    assert chunk_markdown("", caption_re=_FIGURE_CAPTION_RE) == []
    assert chunk_pages([], use_markers=False, caption_re=_FIGURE_CAPTION_RE) == []


def test_chunk_record_defaults():
    r = ChunkRecord(text="t", section=(), page=None)
    assert r.kind == "body"
