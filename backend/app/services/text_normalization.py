"""Repair diacritics that PDF text extraction decomposed into spacing modifiers.

pymupdf4llm emits TeX-style accents as standalone spacing-modifier characters
beside their base letter: the corpus stores `Evs¸en Yanmaz` and `˙Islam G¨uven`
for names that are really `Evşen Yanmaz` and `İslam Güven`.

This is the display-side counterpart to `evals.metadata.compare.normalize`, and
the two are NOT interchangeable. That one folds accents away for comparison and
is deliberately lossy (`G¨uven` -> `guven`); this one restores the real
characters so a name can be shown to a user.
"""

import unicodedata

# Spacing modifier -> (combining mark, binds to the PRECEDING letter).
#
# An explicit allowlist, never a `unicodedata.category(c) == "Sk"` test: the
# ASCII backtick (U+0060) and caret (U+005E) are also category Sk, so a
# category rule would rewrite `NSGA-II` as ǸSGA-II and x^2 as x̂2.
#
# Direction follows where the accent renders. Accents that sit above are
# emitted BEFORE their letter; cedilla and ogonek, which hang below, are
# emitted AFTER it. Verified against both papers in the corpus.
_MODIFIERS: dict[str, tuple[str, bool]] = {
    "¨": ("̈", False),  # diaeresis
    "´": ("́", False),  # acute
    "¯": ("̄", False),  # macron
    "ˆ": ("̂", False),  # circumflex
    "ˇ": ("̌", False),  # caron
    "˘": ("̆", False),  # breve
    "˙": ("̇", False),  # dot above
    "˚": ("̊", False),  # ring above
    "˜": ("̃", False),  # small tilde
    "˝": ("̋", False),  # double acute
    "¸": ("̧", True),  # cedilla  — renders below
    "˛": ("̨", True),  # ogonek   — renders below
}


def recompose_diacritics(text: str) -> str:
    """Rebind stray spacing modifiers onto their base letters.

    Idempotent: text with no spacing modifiers is returned unchanged, so this
    is safe to re-run over already-clean values (the backfill script does).
    """
    if not text:
        return text

    out: list[str] = []
    i = 0
    while i < len(text):
        char = text[i]
        entry = _MODIFIERS.get(char)
        if entry is None:
            out.append(char)
            i += 1
            continue

        mark, binds_to_preceding = entry
        if binds_to_preceding and out and out[-1].isalpha():
            out[-1] = unicodedata.normalize("NFC", out[-1] + mark)
        elif not binds_to_preceding and i + 1 < len(text) and text[i + 1].isalpha():
            out.append(unicodedata.normalize("NFC", text[i + 1] + mark))
            i += 1
        else:
            # No letter to bind to. Keep the character rather than dropping it —
            # this function repairs text, it never deletes content.
            out.append(char)
        i += 1

    return unicodedata.normalize("NFC", "".join(out))
