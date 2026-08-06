"""Verdicts for one extracted metadata field against known truth.

Pure functions of two values, so every case is unit-testable without Postgres,
an LLM, or the live corpus.

`hallucinated` is deliberately its own verdict rather than a flavour of
`wrong`. A venue populated for a paper that has none looks like a filled field
and disappears inside an accuracy percentage — and it is the failure mode this
extractor is most likely to have, because both papers in the dev corpus are
preprints with no stated year and no venue.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

Verdict = Literal["correct", "wrong", "missed", "hallucinated"]

_SPACING_MODIFIER = "Sk"
_NONSPACING_MARK = "Mn"


def normalize(value: str) -> str:
    """Fold a name or venue to a comparison key.

    Spacing modifiers (category Sk: `¨`, `¸`, `˙`) are dropped BEFORE NFKD on
    purpose. NFKD expands U+00A8 DIAERESIS to space + combining mark, so
    decomposing first turns the PDF-mangled 'G¨uven' into 'G uven' — which
    never matches the properly-encoded 'Güven', the exact comparison this
    function exists to make.
    """
    stripped = "".join(c for c in value if unicodedata.category(c) != _SPACING_MODIFIER)
    decomposed = unicodedata.normalize("NFKD", stripped)
    without_marks = "".join(c for c in decomposed if unicodedata.category(c) != _NONSPACING_MARK)
    return re.sub(r"\s+", " ", without_marks).strip().casefold()


def compare_authors(truth: list[str], got: list[str]) -> Verdict:
    """Verdict for a whole author list.

    Order is ignored — a paper's authors are the same set however the extractor
    ordered them. Membership is not: a list missing one author is `wrong`, not
    partially correct, because that is what a user reading the answer gets.
    """
    truth_keys = {k for k in (normalize(x) for x in truth) if k}
    got_keys = {k for k in (normalize(x) for x in got) if k}
    if not truth_keys:
        return "hallucinated" if got_keys else "correct"
    if not got_keys:
        return "missed"
    return "correct" if truth_keys == got_keys else "wrong"


def _scalar_key(value: object | None) -> object | None:
    if value is None:
        return None
    if isinstance(value, str):
        return normalize(value) or None
    return value


def compare_scalar(truth: object | None, got: object | None) -> Verdict:
    """Verdict for a single-valued field (year, venue)."""
    truth_key = _scalar_key(truth)
    got_key = _scalar_key(got)
    if truth_key is None:
        return "hallucinated" if got_key is not None else "correct"
    if got_key is None:
        return "missed"
    return "correct" if truth_key == got_key else "wrong"
