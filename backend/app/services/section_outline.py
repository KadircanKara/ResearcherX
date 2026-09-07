"""Section structure: heading levels, the path stack, and outline markers.

PURE — no DB, no ORM, no settings, no PyMuPDF — like mention_ranker.py and
text_matching.py. The chunker (structured_chunker.py) and the PDF layer
(pdf_extraction.py) both call this; the eval harness can import it too.

Why a heuristic at all: pymupdf4llm emits `##` for section AND subsection
headings (measured across the corpus 2026-09-07 — 1,854 of 2,237 headings
are `##`), so markdown depth carries no hierarchy. The hierarchy is in the
NUMBERING and the ITALICS: `## IV. RL-BASED PLANNER` is a section,
`## _B. Reward Function_` is its subsection. Where a PDF has an outline the
levels come from there instead (see `inject_markers`); this heuristic is
the fallback for the 17% of PDFs without one and for the stored markdown
of papers whose PDF is gone.

Nothing here guesses. A heading that matches no pattern is level 1 with its
own text; a level-2 heading with no level-1 above it is a one-element path.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

_DECOR = re.compile(r"[*_`]+")
_ROMAN = re.compile(r"^[IVXLivxl]+[\.\)]\s+")
_LETTER = re.compile(r"^[A-Za-z][\.\)]\s+")
_DECIMAL = re.compile(r"^(\d+(?:\.\d+)*)[\.\)]?\s+")
_WRAPPED = re.compile(r"^(\*\*|__|\*|_).+?\1\s*$", re.DOTALL)


def clean_heading(text: str) -> str:
    return _DECOR.sub("", text).strip()


def heading_level(text: str, markdown_depth: int, depth_varies: bool) -> int:
    """Level of one heading, from its numbering first and its markdown depth
    second. `depth_varies` is whether the DOCUMENT mixes `#` depths at all —
    an all-`##` paper's depth is noise, not evidence."""
    raw = text.strip()
    stripped = clean_heading(raw)
    if _ROMAN.match(stripped):
        return 1
    if _LETTER.match(stripped) and _WRAPPED.match(raw):
        return 2
    m = _DECIMAL.match(stripped)
    if m:
        return m.group(1).count(".") + 1
    if depth_varies:
        return max(1, markdown_depth)
    return 1


class SectionStack:
    """Turns a sequence of (level, title) into section paths."""

    def __init__(self) -> None:
        self._levels: dict[int, str] = {}

    @property
    def path(self) -> tuple[str, ...]:
        return tuple(self._levels[k] for k in sorted(self._levels))

    def push(self, level: int, title: str) -> tuple[str, ...]:
        self._levels = {k: v for k, v in self._levels.items() if k < level}
        self._levels[level] = title
        return self.path


# ── outline markers ──────────────────────────────────────────────────────
#
# `<!--section 2 "B. Reward Function"-->` on its own line. Chosen over
# rewriting `#` headings because a boundary the outline knows about may
# have NO heading in the markdown at all (bold inline text), and a marker
# can be placed anywhere the anchor lands.

MARKER_RE = re.compile(r'<!--section (?P<level>\d+) (?P<title>"(?:[^"\\]|\\.)*")-->')


def format_marker(level: int, title: str) -> str:
    return f"<!--section {level} {json.dumps(title)}-->"


@dataclass(frozen=True)
class Anchor:
    """Where an outline entry's text starts on a page, as PyMuPDF saw it.

    `leading_words`: the first words of the text block at/below the entry's
    y-coordinate. `block_index`: that block's position among the page's text
    blocks in reading order, the fallback locator when the words are not
    found in the markdown.
    """

    page: int  # 1-based
    level: int
    title: str
    leading_words: tuple[str, ...]
    block_index: int


@dataclass(frozen=True)
class InjectionReport:
    found: int = 0
    by_paragraph: int = 0
    dropped: int = 0


def _words_pattern(words: tuple[str, ...]) -> re.Pattern | None:
    """The leading words, in order, allowing markdown punctuation and
    whitespace between them. Fewer than 2 words is too weak to trust."""
    ws = [w for w in words if w.strip()][:5]
    if len(ws) < 2:
        return None
    gap = r"[\s*_`#]*"
    return re.compile(gap.join(re.escape(w) for w in ws), re.IGNORECASE)


def inject_markers(
    page_texts: list[str], anchors: list[Anchor]
) -> tuple[list[str], InjectionReport]:
    """Insert one marker per anchor into its page's markdown.

    Locator order, each step counted in the report:
      1. the anchor's leading words found in the page markdown → marker
         goes on its own line immediately before the match's line;
      2. not found → the paragraph at `block_index` (paragraphs = `\\n\\n`
         splits) gets the marker prepended;
      3. no such paragraph → the anchor is DROPPED. Its text falls under
         the previous marker. Never attached by title text.
    """
    pages = list(page_texts)
    found = by_para = dropped = 0
    # Process per page, later anchors first, so earlier insert offsets stay valid.
    by_page: dict[int, list[Anchor]] = {}
    for a in anchors:
        by_page.setdefault(a.page, []).append(a)
    for pno, group in by_page.items():
        idx = pno - 1
        if idx < 0 or idx >= len(pages):
            dropped += len(group)
            continue
        text = pages[idx]
        placements: list[tuple[int, str]] = []
        for a in group:
            marker = format_marker(a.level, a.title)
            pat = _words_pattern(a.leading_words)
            m = pat.search(text) if pat else None
            if m:
                line_start = text.rfind("\n", 0, m.start()) + 1
                placements.append((line_start, marker))
                found += 1
                continue
            paras = text.split("\n\n")
            if 0 <= a.block_index < len(paras):
                offset = sum(len(p) + 2 for p in paras[: a.block_index])
                placements.append((offset, marker))
                by_para += 1
            else:
                dropped += 1
        for offset, marker in sorted(placements, key=lambda p: p[0], reverse=True):
            text = text[:offset] + marker + "\n" + text[offset:]
        pages[idx] = text
    return pages, InjectionReport(found=found, by_paragraph=by_para, dropped=dropped)
