"""PDF -> pages + outline + injected markers. PyMuPDF builds the fixture
PDF in-test, so this exercises the real extraction path with no files and
no network."""

import pymupdf

from app.services.pdf_extraction import (
    extract_document,
    outline_from_json,
    outline_to_json,
    pages_from_json,
    pages_to_json,
)
from app.services.section_outline import format_marker
from app.services.structured_chunker import chunk_pages
from app.services.paper_ingest_service import _FIGURE_CAPTION_RE


def _pdf(with_outline: bool) -> bytes:
    doc = pymupdf.open()
    p1 = doc.new_page()
    p1.insert_text((72, 80), "Cooperative Search Paper", fontsize=16)
    p1.insert_text((72, 140), "Abstract. We compare planners.", fontsize=10)
    p1.insert_text((72, 220), "IV. RL-BASED PLANNER", fontsize=12)
    p1.insert_text((72, 250), "The planner learns a policy from episodes.", fontsize=10)
    p2 = doc.new_page()
    p2.insert_text((72, 80), "B. Reward Function", fontsize=12)
    p2.insert_text(
        (72, 110), "The reward is a weighted sum designed to promote search.", fontsize=10
    )
    p2.insert_text((72, 300), "V. RESULTS", fontsize=12)
    p2.insert_text((72, 330), "Figure 3. Reward over training.", fontsize=10)
    if with_outline:
        # `to` is in PDF user space (origin at the page BOTTOM), the same
        # convention get_toc(simple=False) returns. `_outline` converts to
        # top-origin, so give each entry a point just ABOVE its heading text.
        # Re-fetch page 0 via `doc[0]` rather than reusing `p1`: on installed
        # pymupdf 1.28.2, a `Page` object is invalidated once another page is
        # created on the same document (`p1.rect` raises `AssertionError:
        # page is None` after `p2 = doc.new_page()` above) — a fixture bug
        # unrelated to the y-origin arithmetic below.
        h = doc[0].rect.height

        def to(y_from_top: float) -> dict:
            return {"kind": pymupdf.LINK_GOTO, "to": pymupdf.Point(72, h - y_from_top)}

        # `insert_text`'s y is the text BASELINE; the block's reported bbox
        # top (`get_text("blocks")`'s y0) sits ~11-13pt above that baseline
        # for these font sizes (measured: heading inserted at y=220 reports
        # a block y0 of 207). `_anchors` matches the first block whose y0 is
        # at or after the destination y, so the destination must land AT OR
        # ABOVE the block's bbox top, not merely above the baseline -- hence
        # 205/65/285 below (just above each heading's measured y0 of
        # 207/67/287) rather than 10pt off the insertion y, which landed
        # 1-3pt past the block's top and matched the following block instead.

        doc.set_toc(
            [
                [1, "RL-Based Planner", 1, to(205)],
                [2, "Reward Function", 2, to(65)],
                [1, "Results", 2, to(285)],
            ]
        )
    out = doc.tobytes()
    doc.close()
    return out


def test_extracts_pages_with_numbers_and_the_outline():
    ex = extract_document(_pdf(with_outline=True))
    assert [p.page for p in ex.pages] == [1, 2]
    assert ex.has_outline
    assert [(e.level, e.title, e.page) for e in ex.outline] == [
        (1, "RL-Based Planner", 1),
        (2, "Reward Function", 2),
        (1, "Results", 2),
    ]


def test_markers_are_injected_before_the_right_text():
    ex = extract_document(_pdf(with_outline=True))
    p1, p2 = ex.pages[0].text, ex.pages[1].text
    assert p1.index(format_marker(1, "RL-Based Planner")) < p1.index("RL-BASED PLANNER")
    assert p2.index(format_marker(2, "Reward Function")) < p2.index("weighted sum")
    assert p2.index(format_marker(1, "Results")) < p2.index("Figure 3")
    assert ex.injection.found == 3 and ex.injection.dropped == 0


def test_the_plain_markdown_carries_no_markers():
    ex = extract_document(_pdf(with_outline=True))
    assert "<!--section" not in ex.markdown
    assert "weighted sum" in ex.markdown


def test_end_to_end_the_answer_chunk_has_its_path_and_page():
    ex = extract_document(_pdf(with_outline=True))
    recs = chunk_pages(ex.pages, use_markers=ex.has_outline, caption_re=_FIGURE_CAPTION_RE)
    hit = [r for r in recs if "weighted sum" in r.text and r.kind == "body"]
    assert len(hit) == 1
    assert hit[0].section == ("RL-Based Planner", "Reward Function")
    assert hit[0].page == 2
    cap = [r for r in recs if r.kind == "caption"][0]
    assert cap.section == ("Results",) and cap.page == 2


def test_no_outline_means_no_markers_and_headings_take_over():
    ex = extract_document(_pdf(with_outline=False))
    assert not ex.has_outline
    assert all("<!--section" not in p.text for p in ex.pages)
    assert ex.injection.found == 0


def test_json_round_trip_for_pages_and_outline():
    ex = extract_document(_pdf(with_outline=True))
    assert pages_from_json(pages_to_json(ex.pages)) == ex.pages
    assert outline_from_json(outline_to_json(ex.outline)) == ex.outline
