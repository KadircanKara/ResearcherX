"""Deterministic paper resolution — rung 1 of the retrieval scope ladder.

Pure functions: no DB session, no LLM, no I/O. `chat_service.respond()`
already holds every paper of the project in memory, and a project is ~100
papers, so a full scan needs no index, no Postgres extension and no
migration.

CORE SAFETY PROPERTY: this rung resolves only on UNAMBIGUOUS evidence.
Anything ambiguous falls through to the LLM targeter, which sees the whole
question. A false lexical match is the worst outcome available here — it
short-circuits the targeter and scopes confidently to papers the user never
asked about.

WHY EVERY MATCHER ANCHORS ON A CONSTRUCTION, never on bare corpus tokens:
`needs_paper_metadata`'s docstring in chat_service.py records a REVERTED
attempt at corpus-token matching, with live measurements — 10 of 266 real
author surnames collide with common English words ("how", "park", "chen",
"wang", and "how" opens a large share of all questions), and title/venue
tokens are this domain's own subject vocabulary ("learning", "networks",
"control"). The collision set grows silently with every paper added. A
bag-of-words matcher on this corpus is known-broken, not merely risky.
Anchoring on syntax kills a collision class structurally; a blocklist would
rot on the next upload.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.services.text_matching import word_tokens

# A shared contiguous run must reach this many normalized words to resolve a
# paper. Three words of subject vocabulary ("deep reinforcement learning") sit
# inside many titles in this corpus; four contiguous words from one specific
# title are rare.
#
# CORPUS-shaped, not MODEL-shaped: it depends on how repetitive this library's
# titles are, not on the embedding model, so it is a module constant rather
# than a Settings field and `evals/retrieval/resolver_set.json` is what
# re-tunes it. Promote it to Settings only if a real deployment needs a
# different value.
_MIN_SPAN_WORDS = 4


@dataclass(frozen=True)
class ResolvablePaper:
    """Just enough about a paper for the lexical rung to name it."""

    paper_id: str
    title: str
    authors: tuple[str, ...]
    year: int | None


@dataclass(frozen=True)
class SpanMatch:
    """A contiguous title run found in the question, and who claims it.

    More than one `paper_ids` entry is AMBIGUITY, not a multi-paper answer:
    it means one span belongs to several titles. The caller decides what to
    do; this matcher never picks a winner.
    """

    span: str
    paper_ids: tuple[str, ...]


def match_by_title_span(question: str, papers: Sequence[ResolvablePaper]) -> list[SpanMatch]:
    """Contiguous normalized title runs of >=_MIN_SPAN_WORDS found in `question`.

    A paper whose whole title is shorter than the bar matches on its full
    title instead, so a genuinely short title stays resolvable.

    Longest span per paper only: a title contributes at most one SpanMatch,
    keyed on the longest run of it the question contains.
    """
    q_tokens = word_tokens(question)
    if not q_tokens or not papers:
        return []
    q_joined = " ".join(q_tokens)

    by_span: dict[str, list[str]] = {}
    for paper in papers:
        t_tokens = word_tokens(paper.title)
        if not t_tokens:
            continue
        # A short title is matched whole; anything else needs a run at the bar.
        floor = min(_MIN_SPAN_WORDS, len(t_tokens))
        best: str | None = None
        # Longest first, so the first hit for this paper is its longest span.
        for length in range(len(t_tokens), floor - 1, -1):
            for start in range(0, len(t_tokens) - length + 1):
                span = " ".join(t_tokens[start : start + length])
                if _contains_run(q_joined, span):
                    best = span
                    break
            if best is not None:
                break
        if best is not None:
            by_span.setdefault(best, []).append(paper.paper_id)

    return [SpanMatch(span=span, paper_ids=tuple(ids)) for span, ids in by_span.items()]


def _contains_run(haystack: str, needle: str) -> bool:
    """Substring test on WORD boundaries.

    Both sides are already normalized to single-space-separated words, so
    padding with spaces makes a plain `in` a whole-word test: it stops
    "rl a survey" from matching inside "curl a survey".
    """
    return f" {needle} " in f" {haystack} "


# A name may only enter through an attribution construction. This is the whole
# defence against the surname-collision class: "how", "park", "chen" and
# "wang" are all real surnames on this corpus AND common English words, but
# none of them appears inside "by ___" or "___ et al." in ordinary prose.
#
# The capture is deliberately greedy-but-short (1-3 capitalised words): it has
# to reach "Van Der Berg" without swallowing the rest of the sentence.
_ATTRIBUTION_RES = (
    re.compile(r"\bauthored\s+by\s+((?:[A-Z][\w''-]*\s*){1,3})"),
    re.compile(r"\bby\s+((?:[A-Z][\w''-]*\s*){1,3})"),
    re.compile(r"\b([A-Z][\w''-]*)\s+et\s+al\b"),
    re.compile(r"\b([A-Z][\w''-]*)['']s\b"),
)


def _attributed_names(question: str) -> list[str]:
    """Capitalised name spans introduced by an attribution construction.

    Runs against the ORIGINAL question, never the normalized one: the
    capitalisation requirement is the second guard, and normalising first
    would destroy the evidence it depends on.
    """
    names: list[str] = []
    for pattern in _ATTRIBUTION_RES:
        for match in pattern.finditer(question):
            span = match.group(1).strip()
            if span:
                names.append(span)
    return names


def match_by_author(question: str, papers: Sequence[ResolvablePaper]) -> list[str]:
    """Papers whose author list contains a name the question ATTRIBUTES.

    Returns every paper the attributed names cover, in input order. Whether
    several papers is a resolution or an ambiguity is `resolve_papers`'
    decision, not this function's.
    """
    if not papers:
        return []
    name_tokens = {token for name in _attributed_names(question) for token in word_tokens(name)}
    if not name_tokens:
        return []

    matched: list[str] = []
    for paper in papers:
        author_tokens = {token for author in paper.authors for token in word_tokens(author)}
        if name_tokens & author_tokens:
            matched.append(paper.paper_id)
    return matched
