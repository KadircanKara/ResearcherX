"""Pure claim extraction: an answer string -> the sentences a judge must rule on.

Pure (no DB, no LLM, no ORM) like `evals/retrieval/metrics.py`,
`app/services/mention_ranker.py` and `app/services/text_matching.py`, so the
segmentation policy is unit-testable without Postgres or a model. Every
judgement in this harness is denominated in what this module returns, so a
silent change here moves every number the harness reports.

WHY SENTENCES. Groundedness is not a property of an answer, it is a property
of each assertion in it: an answer that is four supported sentences and one
invented one is not "80% true", it is an answer with a hallucination in it,
and a whole-answer verdict cannot say which sentence to go look at. The
citation-correctness half needs the same granularity for a different reason --
a marker is attached to a claim, and asking whether a marker is right is
meaningless without knowing which claim it stands behind.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.citation_attribution import split_prose_segments

# The marker syntax the model writes and every downstream pass reads. One
# number per bracket: `expand_grouped_citations` has already run in production
# before an answer is persisted, so "[8, 14]" cannot reach this module from
# the real pipeline -- and if it ever did, the grouped form would simply not
# match, which is the safe direction (a missed marker under-counts citation
# precision rather than inventing one).
_MARKER_RE = re.compile(r"\[(\d+)\]")

# A sentence boundary: a terminator, optionally trailed by citation markers,
# then whitespace. Markers sit AFTER the terminator about as often as before it
# ("... in the simulation. [4]"), and the trailing run is matched here so the
# split lands after it -- the markers stay with the sentence they belong to.
# `_split_sentences` slices at `match.end()` rather than using `re.split`,
# because `re.split` would DISCARD the separator, deleting those markers from
# the claim entirely while `marker_count` still counted them for the reader.
_BOUNDARY_RE = re.compile(r"(?<=[.!?])((?:\s*\[\d+\])*)\s+")

# Abbreviations whose trailing period is not a sentence end. Deliberately
# short and domain-picked rather than exhaustive: a missed boundary merges two
# sentences into one claim (the judge then rules on both together, which is
# conservative -- the merged claim is unsupported if EITHER half is), while a
# false boundary splits a claim into two fragments that are each unjudgeable.
# The failure directions are not symmetric, so the list errs toward merging.
_ABBREVIATIONS = (
    "et al.",
    "i.e.",
    "e.g.",
    "cf.",
    "vs.",
    "fig.",
    "eq.",
    "sec.",
    "ref.",
    "approx.",
    "no.",
    "resp.",
)

# A "sentence" shorter than this is a fragment, not a claim: a stray "3." left
# by a numbered list, a bare "Yes.", the tail of an abbreviation this module
# failed to guard. Judging them wastes a judge call and pollutes the
# denominator, and none of them can carry a hallucination.
_MIN_CLAIM_CHARS = 25


@dataclass(frozen=True)
class Claim:
    """One sentence of an answer, with the citation markers standing in it.

    `index` is the claim's position in the answer (0-based) and is what the
    judge's verdicts are keyed on -- never the sentence text, which can repeat
    verbatim across an answer and would collapse two claims into one.
    """

    index: int
    text: str
    markers: tuple[int, ...]


def _ends_with_abbreviation(text: str) -> bool:
    lowered = text.lower().rstrip()
    return any(lowered.endswith(abbr) for abbr in _ABBREVIATIONS)


def _split_sentences(prose: str) -> list[str]:
    """Sentence-split one PROSE segment (never a code span).

    Re-joins a split that landed immediately after a known abbreviation, which
    is why this is a manual walk rather than a single `re.split`.
    """
    pieces: list[str] = []
    cursor = 0
    for match in _BOUNDARY_RE.finditer(prose):
        pieces.append(prose[cursor : match.end()])
        cursor = match.end()
    pieces.append(prose[cursor:])

    out: list[str] = []
    for piece in pieces:
        if not piece.strip():
            continue
        if out and _ends_with_abbreviation(out[-1]):
            out[-1] = f"{out[-1]} {piece.strip()}"
            continue
        out.append(piece.strip())
    return out


def extract_claims(answer: str) -> list[Claim]:
    """The judgeable sentences of `answer`, in order.

    Code is excluded via `citation_attribution.split_prose_segments` -- the
    single owner of "what counts as code" in this codebase, shared rather than
    re-implemented for the same reason the strip and the renumbering share it:
    two copies of that guard drift, and a divergence corrupts exactly the bytes
    that must be treated as code. A fenced block is not a claim about a paper
    and has no truth value to rule on; a `[4]` inside one is an array index,
    not a citation.

    Markdown structure (headings, list bullets) is left in the claim text
    rather than stripped: the judge reads it as context, and stripping it would
    make "- **Latency**: 42 ms [3]" indistinguishable from prose asserting the
    same thing, which it is.
    """
    claims: list[Claim] = []
    for segment, is_code in split_prose_segments(answer):
        if is_code:
            continue
        for sentence in _split_sentences(segment):
            if len(sentence) < _MIN_CLAIM_CHARS:
                continue
            markers = tuple(int(m.group(1)) for m in _MARKER_RE.finditer(sentence))
            claims.append(Claim(index=len(claims), text=sentence, markers=markers))
    return claims


def marker_count(answer: str) -> int:
    """Every citation marker standing in PROSE, including those inside
    sentences too short to be claims.

    Separate from `sum(len(c.markers) for c in extract_claims(...))` on
    purpose: that sum is the denominator for citation precision (a marker can
    only be judged against the claim it stands behind), while this is the
    total the answer actually shows the reader. Reporting them apart is what
    makes "markers the harness could not judge" visible instead of silently
    absent.
    """
    total = 0
    for segment, is_code in split_prose_segments(answer):
        if is_code:
            continue
        total += len(_MARKER_RE.findall(segment))
    return total
