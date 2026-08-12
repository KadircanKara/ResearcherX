"""Lossy text folding for COMPARISON.

The display-side counterpart is `text_normalization.recompose_diacritics`,
which restores real accented characters for showing a name to a user. This
module goes the other way and is deliberately lossy: `Güven` -> `guven`, so a
question typed without diacritics still matches a title extracted from a PDF
that has them.

`evals/metadata/compare.py::normalize` does a similar job for metadata
scoring. It is NOT imported here and this is not imported there: `app` must
never import from `evals`, and the two fold for different purposes — that one
compares extracted metadata against ground truth, this one matches a user's
prose against a title.
"""

import re
import unicodedata

# Anything that is not a letter or a digit becomes a space. Hyphens,
# apostrophes, colons and em-dashes all vary between how a title is printed in
# a PDF and how a user types it, so none of them may carry meaning here.
_NON_ALNUM = re.compile(r"[^0-9a-z]+")


def normalize_for_match(text: str) -> str:
    """Casefold, strip accents, reduce punctuation to spaces, collapse runs.

    Idempotent: the output contains only lowercase alphanumerics and single
    spaces, so re-normalising it is a no-op.
    """
    if not text:
        return ""
    # NFKD splits an accented character into base + combining mark; dropping
    # category Mn then leaves the bare letter. Casefold first so the ASCII
    # test below is the only case rule in play.
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return _NON_ALNUM.sub(" ", stripped).strip()


def word_tokens(text: str) -> list[str]:
    """Normalized words, in order. Empty list for empty or symbol-only input."""
    normalized = normalize_for_match(text)
    return normalized.split() if normalized else []
