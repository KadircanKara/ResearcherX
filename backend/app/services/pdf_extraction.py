"""PDF bytes -> per-page markdown, outline, and outline markers.

The ONLY module in the chunking path that touches PyMuPDF. Everything it
produces is plain data (`PageText`, `OutlineEntry`) that the pure chunker
consumes and that `papers.extracted_pages` / `papers.outline` persist, so
a re-chunk never needs the PDF again.

Tier selection (spec §1) is decided here by ONE fact: does the PDF carry an
outline. With one, its entries become `<!--section …-->` markers injected
into the page markdown by COORDINATE (measured 2026-09-08 on an 18-paper
sample: 98% of text blocks attached, 64% two-level, against 41% for
heading-text matching — many outline entries have no `#` heading in the
markdown at all, and only a coordinate finds those). Without one, the
markdown headings and `section_outline.heading_level` take over downstream.

`pymupdf4llm`'s own `TocHeaders` was tested 2026-09-08 and does NOT
propagate outline depth into heading levels (every heading still `##`).
Do not retry it as a shortcut.
"""

from __future__ import annotations

from dataclasses import dataclass

import pymupdf
import pymupdf4llm

from app.services.section_outline import Anchor, InjectionReport, inject_markers
from app.services.structured_chunker import PageText

_LEADING_WORDS = 6


@dataclass(frozen=True)
class OutlineEntry:
    level: int
    title: str
    page: int  # 1-based
    y: float  # distance from the page TOP, PDF points (converted here; see _outline)


@dataclass(frozen=True)
class ExtractedDocument:
    pages: list[PageText]  # with markers when has_outline
    outline: list[OutlineEntry]
    markdown: str  # plain join, no markers (for papers.extracted_text)
    has_outline: bool
    injection: InjectionReport


def _outline(doc: pymupdf.Document) -> list[OutlineEntry]:
    """Outline entries with `y` measured from the page TOP.

    `get_toc(simple=False)` returns the destination point in PDF user space,
    origin at the page BOTTOM. `get_text("blocks")` reports top-origin
    coordinates. The spike's 98% attachment (2026-09-08, pos.py) relied on
    exactly this `height - y` conversion; without it every anchor lands on
    the wrong block and attachment collapses silently.
    """
    out: list[OutlineEntry] = []
    for entry in doc.get_toc(simple=False):
        level, title, page = int(entry[0]), str(entry[1]), int(entry[2])
        dest = entry[3] if len(entry) > 3 and isinstance(entry[3], dict) else {}
        if page < 1 or page > doc.page_count:
            continue
        to = dest.get("to")
        y_bottom = float(getattr(to, "y", 0.0)) if to is not None else 0.0
        y_from_top = float(doc[page - 1].rect.height) - y_bottom
        out.append(OutlineEntry(level=level, title=title.strip(), page=page, y=y_from_top))
    return out


def _anchors(doc: pymupdf.Document, outline: list[OutlineEntry]) -> list[Anchor]:
    """For each outline entry, the first text block at or below its y."""
    anchors: list[Anchor] = []
    blocks_by_page: dict[int, list[tuple[float, str]]] = {}
    for e in outline:
        if e.page not in blocks_by_page:
            page = doc[e.page - 1]
            blocks = [(b[1], b[4]) for b in page.get_text("blocks") if b[6] == 0 and b[4].strip()]
            blocks.sort(key=lambda b: b[0])
            blocks_by_page[e.page] = blocks
        blocks = blocks_by_page[e.page]
        idx = next((i for i, (y0, _) in enumerate(blocks) if y0 >= e.y - 2), None)
        if idx is None:
            continue
        words = tuple(blocks[idx][1].split()[:_LEADING_WORDS])
        anchors.append(
            Anchor(page=e.page, level=e.level, title=e.title, leading_words=words, block_index=idx)
        )
    return anchors


def extract_document(pdf_bytes: bytes) -> ExtractedDocument:
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        raw_pages = pymupdf4llm.to_markdown(doc, page_chunks=True)
        pages = [
            PageText(page=int(p["metadata"]["page_number"]), text=p["text"]) for p in raw_pages
        ]
        outline = _outline(doc)
        markdown = "\n\n".join(p.text for p in pages)
        if not outline:
            return ExtractedDocument(pages, [], markdown, False, InjectionReport())
        injected, report = inject_markers([p.text for p in pages], _anchors(doc, outline))
        pages = [PageText(p.page, t) for p, t in zip(pages, injected, strict=True)]
        return ExtractedDocument(pages, outline, markdown, True, report)
    finally:
        doc.close()


# ── persistence shapes for papers.extracted_pages / papers.outline ────────


def pages_to_json(pages: list[PageText]) -> list[dict]:
    return [{"page": p.page, "text": p.text} for p in pages]


def pages_from_json(raw: list[dict] | None) -> list[PageText]:
    return [PageText(page=r.get("page"), text=r["text"]) for r in (raw or [])]


def outline_to_json(outline: list[OutlineEntry]) -> list[dict]:
    return [{"level": e.level, "title": e.title, "page": e.page, "y": e.y} for e in outline]


def outline_from_json(raw: list[dict] | None) -> list[OutlineEntry]:
    return [
        OutlineEntry(level=int(r["level"]), title=r["title"], page=int(r["page"]), y=float(r["y"]))
        for r in (raw or [])
    ]
