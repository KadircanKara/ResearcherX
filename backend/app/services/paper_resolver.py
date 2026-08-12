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


# U+2019 RIGHT SINGLE QUOTATION MARK, built with chr() rather than written as
# a literal. Smart-quote autocorrect turns a typed "Hopper's" into "Hopper's",
# so the class has to accept both -- but a literal ' in this source has now
# been silently flattened to ASCII twice in transcription, taking the guard
# with it and leaving a test that passed while asserting nothing. chr() is
# all-ASCII source: there is nothing left for a transcription step to eat.
_RSQUO = chr(0x2019)
_NAME_CHARS = rf"[\w'{_RSQUO}-]"
_APOSTROPHE = rf"['{_RSQUO}]"

_ATTRIBUTION_RES = (
    re.compile(rf"\bauthored\s+by\s+((?:[^\W\d_]{_NAME_CHARS}*\s*){{1,3}})"),
    re.compile(rf"\bby\s+((?:[^\W\d_]{_NAME_CHARS}*\s*){{1,3}})"),
    re.compile(rf"\b([^\W\d_]{_NAME_CHARS}*)\s+et\s+al\b"),
    re.compile(rf"\b([^\W\d_]{_NAME_CHARS}*){_APOSTROPHE}s\b"),
)


def _leading_capitalised(span: str) -> str:
    """The leading run of capitalised words in `span`.

    THE capitalisation guard, and it runs on the ORIGINAL question text:
    "by wang market" is ordinary prose, "by Wang" is an attribution. Trimming
    at the first lowercase word is what keeps the regex's any-letter class
    from admitting prose.
    """
    kept: list[str] = []
    for word in span.split():
        if not word[:1].isupper():
            break
        kept.append(word)
    return " ".join(kept)


def _attributed_names(question: str) -> list[str]:
    """Capitalised name spans introduced by an attribution construction.

    Runs against the ORIGINAL question, never the normalized one: the
    capitalisation requirement is the second guard, and normalising first
    would destroy the evidence it depends on.
    """
    names: list[str] = []
    for pattern in _ATTRIBUTION_RES:
        for match in pattern.finditer(question):
            span = _leading_capitalised(match.group(1).strip())
            if span:
                names.append(span)
    return names


def match_by_author(question: str, papers: Sequence[ResolvablePaper]) -> list[str]:
    """Papers whose author list contains a name the question ATTRIBUTES.

    Returns every paper the attributed names cover, in input order. Whether
    several papers is a resolution or an ambiguity is `resolve_papers`'
    decision, not this function's.

    LONGEST MATCHING PREFIX, not any-token overlap. The regex captures up to
    three capitalised words, so it can sweep in a capitalised word that is not
    part of the name at all -- "by Wang Turing award winners" captures
    "Wang Turing". Overlap matching then hit Turing's paper too: a false match,
    the exact class this module exists to prevent. Taking the longest prefix
    that is fully contained in ONE paper's author tokens rejects the stray
    word while still resolving the real name.
    """
    if not papers:
        return []
    tokens_by_paper = {
        paper.paper_id: {token for author in paper.authors for token in word_tokens(author)}
        for paper in papers
    }
    matched: set[str] = set()
    for name in _attributed_names(question):
        tokens = word_tokens(name)
        for length in range(len(tokens), 0, -1):
            prefix = set(tokens[:length])
            hits = [p.paper_id for p in papers if prefix <= tokens_by_paper[p.paper_id]]
            if hits:
                matched.update(hits)
                break
    return [p.paper_id for p in papers if p.paper_id in matched]


# Bare four-digit years only, 1900-2099. Anchored on word boundaries so
# "equation 42" and "199 agents" contribute nothing. Mirrors the _YEAR_RE
# already used by needs_paper_metadata in chat_service.py.
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")


def match_by_year(question: str, papers: Sequence[ResolvablePaper]) -> list[str]:
    """Papers whose `year` is named literally in the question, in input order."""
    years = {int(m.group(0)) for m in _YEAR_RE.finditer(question)}
    if not years:
        return []
    return [p.paper_id for p in papers if p.year is not None and p.year in years]


def resolve_papers(
    question: str, papers: Sequence[ResolvablePaper], *, max_papers: int
) -> list[str]:
    """Paper ids this question names outright, or [] to fall through.

    [] is the normal, expected answer: it means the LLM targeter should
    decide. This function never guesses — see the module docstring's core
    safety property.
    """
    if len(papers) < 2:
        # Nothing to disambiguate: a single-paper project scopes to that paper
        # whichever way the decision is made, so this rung has no work to do.
        return []

    spans = match_by_title_span(question, papers)
    if spans:
        if any(len(s.paper_ids) > 1 for s in spans):
            # One span, several titles. Falling through ENTIRELY (rather than
            # keeping the unambiguous spans) hands the whole question to the
            # targeter, which can read the surrounding words this matcher
            # cannot.
            return []
        resolved = [s.paper_ids[0] for s in spans]
        return _capped(resolved, max_papers)

    by_author = match_by_author(question, papers)
    by_year = match_by_year(question, papers)

    if by_author:
        narrowed = [pid for pid in by_author if pid in set(by_year)] if by_year else by_author
        # An author covering several papers is ambiguity, not a set: the
        # targeter can use a topical description ("the paper about swarm
        # coordination by Yanmaz") that no author match can see.
        return _capped(narrowed, max_papers) if len(narrowed) == 1 else []

    # A year alone resolves only when it is unique in the project.
    return _capped(by_year, max_papers) if len(by_year) == 1 else []


def _capped(paper_ids: list[str], max_papers: int) -> list[str]:
    """Drop the whole result when it is too large to be a scoping signal.

    Truncating instead would silently answer about an arbitrary subset of the
    papers the question named, which is worse than falling through.
    """
    return paper_ids if 1 <= len(paper_ids) <= max_papers else []
