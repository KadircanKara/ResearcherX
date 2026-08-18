"""Deterministic enforcement of citation provenance.

The invariant this module exists to hold:

    A citation marker may appear only in prose attributed to the paper whose
    chunk it actually is.

Observed live on 2026-08-15: a turn retrieved five chunks, all from ONE paper,
and the answer enumerated four papers, hanging those five markers off all four
list items. Three of the four items therefore carried a citation chip labelled
with a paper that does not support them -- two of those papers had no chunk in
the turn's evidence at all; their titles came from the PAPERS block, which
ships every project paper's title.

Enforcement is post-hoc and deterministic ON PURPOSE, not a fourth prompt rule.
Both preconditions for the model getting this right were already met: the
excerpt catalog labels every excerpt with its own paper, and chat_agent.SYSTEM
already states that an answer drawn from the PAPERS block takes no citation.
The model had the information, had the instruction, and violated both. See
chat_agent's ORDER IS DELIBERATE comment for how brittle that prompt stack
already is.

Every failure direction here is *don't enforce*, never *strip something valid*:
a missed misattribution is the status quo, a wrongly stripped citation is new
harm. Hence the four-word title anchor, ties opening no span, inline title
mentions opening nothing, and out-of-range markers being left alone.
"""

import re
from collections.abc import Mapping, Sequence

_CITATION_RE = re.compile(r"\[(\d+)\]")

# A fence opens with ``` or ~~~ — both are valid markdown fences, and remark
# (the frontend's markdown renderer) treats either as <pre><code>. The
# backreference (\1) means a fence can only be closed by the SAME delimiter
# it opened with — a ``` fence is never closed by ~~~ or vice versa. A fence
# with no matching closing delimiter runs to the end of the text rather than
# falling through to be reinterpreted as (part of) an inline span. Fences are
# located in a pass of their own, before inline spans are considered at all,
# so a stray or unpaired backtick elsewhere in the answer can never pair
# across a fence delimiter. See renumber_citations' docstring for why a
# single combined pattern got this wrong.
_FENCE_RE = re.compile(r"(```|~~~).*?(?:\1|\Z)", re.DOTALL)

# Inline spans are matched only within the prose _FENCE_RE leaves behind, so
# a backtick bordering a fence can no longer be mistaken for the other half
# of an inline span.
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")

# Known gap, left deliberately: a four-space indented run is markdown code
# too (remark renders it as <pre><code>, same as a fence), but this guard
# does not detect it. Whether an indented run is code or list-item
# continuation content depends on the enclosing list's nesting column, which
# needs list-context state this function does not track — and this chat's
# system prompt asks for "-" bullets and fenced code, not indented blocks, so
# nested-bullet content is the routine case and a genuine indented code block
# is not. Treating indentation as code by itself would risk the opposite,
# worse failure: a nested bullet's citation silently skipped from the numbered
# sequence ([1], [3], no [2]) rather than merely mis-renumbered. See
# test_an_indented_code_block_is_a_documented_gap_not_detected_as_code for the
# accepted current behaviour.


def segment_offsets(text: str) -> list[tuple[int, int, bool]]:
    """Split `text` into (start, end, is_code) spans covering it exactly.

    The single owner of "what counts as code" for every pass that rewrites an
    answer's citation markers. Duplicating this guard was rejected: it is
    subtle enough that two copies will diverge, and a divergence corrupts
    exactly the bytes the backend and the frontend must agree are code.

    Fences and inline spans are found in two separate passes rather than one
    combined pattern. A single alternation tried left to right lets an
    unterminated fence fall through to the inline alternative — consuming two
    of its three backticks as an empty span — and lets a stray backtick
    earlier in the answer pair with a fence's own opening backtick; both leak
    a fenced marker out into rewriting. Locating fences first, over the whole
    text, with "no closing ``` " meaning "runs to end of text" rather than
    "not a fence", removes both failure modes: fence boundaries never depend
    on where a stray backtick happens to sit, and an answer truncated
    mid-snippet — an observed occurrence, not a hypothetical: a chat reply hit
    `finish_reason=length` mid-sentence on 2026-08-10 — still treats
    everything after the opening fence as code.
    """
    # Stage 1: split the whole text on fenced blocks. An unterminated fence
    # consumes to the end of the text instead of un-matching.
    fenced: list[tuple[int, int, bool]] = []
    cursor = 0
    for match in _FENCE_RE.finditer(text):
        if match.start() > cursor:
            fenced.append((cursor, match.start(), False))
        fenced.append((match.start(), match.end(), True))
        cursor = match.end()
    fenced.append((cursor, len(text), False))

    # Stage 2: within what Stage 1 left as prose, split on inline spans. Code
    # spans pass through untouched — a fence is never re-examined here, so a
    # backtick bordering one cannot pair across the boundary.
    out: list[tuple[int, int, bool]] = []
    for start, end, is_code in fenced:
        if is_code:
            out.append((start, end, True))
            continue
        inner = start
        for match in _INLINE_CODE_RE.finditer(text, start, end):
            if match.start() > inner:
                out.append((inner, match.start(), False))
            out.append((match.start(), match.end(), True))
            inner = match.end()
        out.append((inner, end, False))
    return out


def split_prose_segments(text: str) -> list[tuple[str, bool]]:
    """`segment_offsets` as (body, is_code) slices. Concatenates back to `text`."""
    return [(text[start:end], is_code) for start, end, is_code in segment_offsets(text)]


# The head of an attribution span: a markdown list item or a heading. "List
# item" is a line whose first non-whitespace content is -, * or + (the bullets
# the chat prompt asks for) or "<digits>." (the ordered form the live answer
# used). Nesting depth is not tracked: any qualifying line closes the previous
# span, so enforcement never leaks across an item it could not attribute.
_HEAD_LINE_RE = re.compile(r"^[ \t]*(?:[-*+][ \t]|\d+\.[ \t]|#)")

# A maximal run of markers bound by commas/semicolons: "[2], [3]" is one run,
# and so is "[1] [2]". "and"/"or" do NOT bind — see _rewrite_run.
_RUN_RE = re.compile(r"\[\d+\](?:[ \t]*[,;]?[ \t]*\[\d+\])*")

_WORD_RE = re.compile(r"\w+", re.UNICODE)

# Four contiguous title words is the anchor strength this project already
# reasons about for lexical title matching (see paper scope resolution). Below
# it, ordinary sentences collide with title fragments.
_MIN_TITLE_WORDS = 4


def _words(value: str) -> list[str]:
    """Normalize to comparison tokens: lowercase, punctuation stripped."""
    return _WORD_RE.findall(value.lower())


def _longest_common_run(needle: Sequence[str], haystack: Sequence[str]) -> int:
    """Length of the longest contiguous token run present in both sequences."""
    if not needle or not haystack:
        return 0
    best = 0
    previous = [0] * (len(haystack) + 1)
    for token in needle:
        current = [0] * (len(haystack) + 1)
        for j, other in enumerate(haystack, start=1):
            if token == other:
                current[j] = previous[j - 1] + 1
                if current[j] > best:
                    best = current[j]
        previous = current
    return best


def _head_paper(line: str, title_words: Mapping[str, list[str]]) -> str | None:
    """Which paper, if any, this head line attributes its span to.

    Keyed on the first line rather than the item's start because the real
    answer shape puts the title after a bold label:
    `1. **Title:** Cooperative Multi-Target Search with UAV Swarms: ...`

    Both resolution rules fail toward non-enforcement: longest match wins, and
    a TIE OPENS NO SPAN — two library papers sharing a four-word run leave the
    position ambiguous, and ambiguity is never resolved by guessing.
    """
    line_words = _words(line)
    best_len = 0
    winners: list[str] = []
    for paper_id, words in title_words.items():
        run = _longest_common_run(words, line_words)
        if run < _MIN_TITLE_WORDS or run < best_len:
            continue
        if run > best_len:
            best_len, winners = run, [paper_id]
        else:
            winners.append(paper_id)
    return winners[0] if len(winners) == 1 else None


def _spans(text: str, title_words: Mapping[str, list[str]]) -> list[tuple[int, str | None]]:
    """(offset, paper_id) span starts, in order. paper_id None = unattributed.

    A qualifying head line always CLOSES the previous span; only one that
    resolves to a title opens a new one. A list item naming no paper therefore
    turns enforcement off rather than inheriting the item above it.
    """
    code = [(start, end) for start, end, is_code in segment_offsets(text) if is_code]
    spans: list[tuple[int, str | None]] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        start = offset
        offset += len(line)
        if not _HEAD_LINE_RE.match(line):
            continue
        # A "- " inside a fenced block is code, not a list item.
        if any(code_start <= start < code_end for code_start, code_end in code):
            continue
        spans.append((start, _head_paper(line, title_words)))
    return spans


def _paper_at(spans: Sequence[tuple[int, str | None]], offset: int) -> str | None:
    """The paper governing `offset`, or None where nothing is attributed.

    Prose before the first span head is unattributed, and its markers are left
    alone.
    """
    current: str | None = None
    for start, paper_id in spans:
        if start > offset:
            break
        current = paper_id
    return current


def _rewrite_run(run: str, keep: list[str]) -> str:
    """Rebuild a marker run from the markers that survived.

    Markers do not sit alone in prose — the live answer ends a sentence
    `... demand hotspots [2], [3].`, and removing only the bracketed tokens
    leaves `... demand hotspots , .`, which looks worse than the bug. So a run
    is rewritten whole: its binding separators belong to the run, not to the
    sentence, and sentence-ending punctuation is never touched.

    A run that loses every marker is deleted along with the whitespace that
    preceded it (handled by the caller). Survivors are rejoined with the run's
    own separator style.
    """
    if not keep:
        return ""
    separator = ", " if "," in run else ("; " if ";" in run else " ")
    return separator.join(keep)


# A grouped marker: two or more catalog positions inside ONE bracket, which is
# how the model writes two sources for a single claim ("[8, 14]"). Every marker
# pattern in this system matches one number per bracket -- backend renumbering,
# the strip above, and the FRONTEND's own MARKER regex in lib/citation-marks.ts
# -- so before expansion a grouped marker was invisible to all three at once:
# it kept its raw CATALOG positions in the delivered answer (numbers the reader
# never sees, pointing at nothing), earned no entry in the citations array, and
# rendered as prose with no chips.
_GROUPED_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)+)\]")


def expand_grouped_citations(text: str, max_n: int) -> str:
    """Rewrite "[8, 14]" as "[8], [14]" so every pass can see both markers.

    Expansion rather than teaching four separate patterns to parse groups: one
    normalization at the front of the pipeline leaves renumbering, the strip
    and the frontend all working on the single-number markers they already
    handle correctly. It runs BEFORE the strip, so a grouped marker standing in
    prose attributed to another paper is now strippable per-number.

    ", " is the joiner because the frontend groups markers separated by
    whitespace and punctuation into one run -- the reader still sees one group
    of chips, and the hover card still steps through all of them.

    GATED ON max_n, and that gate is the whole precision story: a bracketed
    number pair is only a citation run if every number in it is a position the
    catalog actually issued. Without it "[2023, 2024]" -- a year range in
    ordinary prose -- would be expanded and then rewritten into two
    "[source unavailable]" markers, mangling text that was never a citation.
    A group holding even one out-of-range number is left entirely alone.

    Code is never touched: `segment_offsets` owns that judgement for this
    module, so `matrix[1, 2]` inside a fence or an inline span survives.
    """
    if max_n <= 0 or "," not in text:
        return text

    out: list[str] = []
    cursor = 0
    for seg_start, seg_end, is_code in segment_offsets(text):
        if is_code:
            continue
        for match in _GROUPED_RE.finditer(text, seg_start, seg_end):
            numbers = [int(part) for part in match.group(1).split(",")]
            if not all(0 < n <= max_n for n in numbers):
                continue
            out.append(text[cursor : match.start()])
            out.append(", ".join(f"[{n}]" for n in numbers))
            cursor = match.end()
    out.append(text[cursor:])
    return "".join(out)


def strip_misattributed_citations(
    text: str,
    *,
    chunk_papers: Mapping[int, str],
    paper_titles: Mapping[str, str],
) -> tuple[str, list[int]]:
    """Remove citation markers from prose attributed to a different paper.

    `chunk_papers` maps catalog position -> paper_id (pre-renumbering, which is
    what the model cited by). `paper_titles` maps paper_id -> title for EVERY
    project paper, not only cited ones: in the live case two of the three
    offending spans were headed by papers with zero chunks in the turn's
    evidence, so cited-only matching would have missed them entirely.

    The surrounding prose is left exactly as written. The claim survives as
    uncited text rather than as a false citation — this is a redaction, not an
    editor. It removes the false PROVENANCE, not the unsupported claim.

    Returns the cleaned text and the stripped marker numbers (for logging, not
    for the caller's control flow).
    """
    if not text or not paper_titles:
        return text, []

    title_words = {
        paper_id: words
        for paper_id, title in paper_titles.items()
        if len(words := _words(title or "")) >= _MIN_TITLE_WORDS
    }
    if not title_words:
        return text, []

    spans = _spans(text, title_words)
    if not any(paper_id for _, paper_id in spans):
        return text, []

    stripped: list[int] = []
    out: list[str] = []
    cursor = 0
    for seg_start, seg_end, is_code in segment_offsets(text):
        if is_code:
            continue
        for run in _RUN_RE.finditer(text, seg_start, seg_end):
            owner = _paper_at(spans, run.start())
            if owner is None:
                continue
            keep: list[str] = []
            dropped: list[int] = []
            for marker in _CITATION_RE.finditer(run.group(0)):
                n = int(marker.group(1))
                # An unknown marker points at no chunk of ours: out of range,
                # or a number the catalog never issued. renumber_citations
                # already replaces those, and guessing here would strip a
                # marker on no evidence.
                if chunk_papers.get(n, owner) == owner:
                    keep.append(marker.group(0))
                else:
                    dropped.append(n)
            if not dropped:
                continue
            stripped.extend(dropped)
            # Only when the run vanishes entirely does its leading whitespace
            # go with it, so `hotspots [2], [3].` becomes `hotspots.` and not
            # `hotspots .`.
            start = run.start()
            if not keep:
                while start > cursor and text[start - 1] in " \t":
                    start -= 1
            out.append(text[cursor:start])
            out.append(_rewrite_run(run.group(0), keep))
            cursor = run.end()
    out.append(text[cursor:])
    return "".join(out), stripped
