"""Section-bounded chunking with page attribution.

PURE: no DB, no ORM, no settings, no PyMuPDF. Consumes text the extraction
layer already produced (`pdf_extraction.py` for PDFs, `papers.extracted_text`
for tier 3) and returns `ChunkRecord`s. `index_chunks` persists them.

    pages ─ join ─> one document + page offsets
                    ├─ segments: cut at every section boundary
                    │     tier 1: `<!--section L "title"-->` markers
                    │     tiers 2/3: `#` headings + section_outline.heading_level
                    ├─ split each segment with RecursiveCharacterTextSplitter
                    └─ captions, each with the section/page it was lifted from

Why the splitter runs INSIDE a segment and never over the whole document:
`RecursiveCharacterTextSplitter` tries `\\n\\n` first, and a heading is
followed by a blank line, so the cut right after a heading is its PREFERRED
split. Used naively it orphans headings more often than the word window it
replaces. The section boundary has to come first; the splitter only decides
where a long section breaks.

CHUNK_SIZE/CHUNK_OVERLAP are characters, chosen so the median chunk stays
near the previous 384-word (~2,400 char, ~439 token) window — the
`max_context_chunks` budget was measured against that size. A section
shorter than CHUNK_SIZE is one chunk; that is decision 6 of the spec (keep
small sections, measure before merging).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.services.section_outline import (
    MARKER_RE,
    SectionStack,
    clean_heading,
    heading_level,
)

CHUNK_SIZE = 2400
CHUNK_OVERLAP = 300

_HEADING_RE = re.compile(r"^(#{1,4})[ \t]+(.+?)[ \t]*$", re.M)
_PAGE_SEP = "\n\n"


@dataclass(frozen=True)
class PageText:
    page: int | None
    text: str


@dataclass(frozen=True)
class Segment:
    section: tuple[str, ...]
    start: int  # offset of `text` in the joined document
    text: str


@dataclass(frozen=True)
class ChunkRecord:
    text: str
    section: tuple[str, ...]
    page: int | None
    kind: str = "body"  # "body" | "caption" | "abstract"


def join_pages(pages: list[PageText]) -> tuple[str, list[tuple[int, int | None]]]:
    """One document string plus `[(start_offset, page)]`."""
    parts: list[str] = []
    starts: list[tuple[int, int | None]] = []
    offset = 0
    for i, p in enumerate(pages):
        if i:
            offset += len(_PAGE_SEP)
        starts.append((offset, p.page))
        parts.append(p.text)
        offset += len(p.text)
    return _PAGE_SEP.join(parts), starts


def page_at(page_starts: list[tuple[int, int | None]], offset: int) -> int | None:
    page: int | None = None
    for start, p in page_starts:
        if start <= offset:
            page = p
        else:
            break
    return page


def _boundaries(doc: str, *, use_markers: bool) -> list[tuple[int, int, int, str]]:
    """`(match_start, match_end, level, title)` for every boundary line."""
    out: list[tuple[int, int, int, str]] = []
    if use_markers:
        for m in MARKER_RE.finditer(doc):
            end = m.end()
            if end < len(doc) and doc[end] == "\n":
                end += 1
            out.append((m.start(), end, int(m.group("level")), json.loads(m.group("title"))))
        return out
    heads = list(_HEADING_RE.finditer(doc))
    depths = {len(m.group(1)) for m in heads}
    depth_varies = len(depths) > 1
    for m in heads:
        level = heading_level(m.group(2), len(m.group(1)), depth_varies)
        end = m.end()
        if end < len(doc) and doc[end] == "\n":
            end += 1
        out.append((m.start(), end, level, clean_heading(m.group(2))))
    return out


def segments_from_text(doc: str, *, use_markers: bool) -> list[Segment]:
    """Cut `doc` at every boundary. The boundary line itself belongs to no
    segment; the path is what it contributes."""
    bounds = _boundaries(doc, use_markers=use_markers)
    stack = SectionStack()
    segments: list[Segment] = []
    cursor = 0
    path: tuple[str, ...] = ()
    for b_start, b_end, level, title in bounds:
        body = doc[cursor:b_start]
        if body.strip():
            segments.append(Segment(path, cursor, body))
        path = stack.push(level, title)
        cursor = b_end
    tail = doc[cursor:]
    if tail.strip():
        segments.append(Segment(path, cursor, tail))
    return segments


def _splitter() -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        add_start_index=True,
    )


def split_segments(
    segments: list[Segment], page_starts: list[tuple[int, int | None]]
) -> list[ChunkRecord]:
    splitter = _splitter()
    records: list[ChunkRecord] = []
    for seg in segments:
        # create_documents, not split_text: only the former sets start_index.
        for d in splitter.create_documents([seg.text]):
            text = d.page_content.strip()
            if not text:
                continue
            offset = seg.start + int(d.metadata.get("start_index", 0))
            records.append(
                ChunkRecord(text=text, section=seg.section, page=page_at(page_starts, offset))
            )
    return records


def _section_at(segments: list[Segment], offset: int) -> tuple[str, ...]:
    section: tuple[str, ...] = ()
    for seg in segments:
        if seg.start <= offset:
            section = seg.section
        else:
            break
    return section


def caption_records(
    doc: str,
    segments: list[Segment],
    page_starts: list[tuple[int, int | None]],
    caption_re: re.Pattern,
) -> list[ChunkRecord]:
    """Each figure caption as its own chunk, tagged with where it sits."""
    return [
        ChunkRecord(
            text=m.group(0).strip(),
            section=_section_at(segments, m.start()),
            page=page_at(page_starts, m.start()),
            kind="caption",
        )
        for m in caption_re.finditer(doc)
    ]


def chunk_pages(
    pages: list[PageText], *, use_markers: bool, caption_re: re.Pattern
) -> list[ChunkRecord]:
    """Body chunks in document order, then captions — the order `ingest()`
    has always used, so a "Figure 3" query still hits the caption directly."""
    if not pages:
        return []
    doc, starts = join_pages(pages)
    if not doc.strip():
        return []
    segments = segments_from_text(doc, use_markers=use_markers)
    return split_segments(segments, starts) + caption_records(doc, segments, starts, caption_re)


def chunk_markdown(md: str, caption_re: re.Pattern) -> list[ChunkRecord]:
    """Tier 3: stored markdown, no pages, `#` headings for structure."""
    return chunk_pages([PageText(None, md)], use_markers=False, caption_re=caption_re)
