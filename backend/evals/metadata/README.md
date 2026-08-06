# Metadata eval harness

Measures whether extraction got each paper's **authors, year, and venue** right,
against the **live dev database**. Measures only — it never writes.

    docker compose exec -T backend python -m evals.metadata.run_eval
    docker compose exec -T backend python -m evals.metadata.run_eval --set /path/to/other.json

The `-m` form is required: `pyproject.toml` packages only `app*`, so `evals`
isn't installed and running it by file path fails with
`ModuleNotFoundError: No module named 'app'`.

## Why this exists

Extraction runs through an LLM, and the test suite deliberately cannot reach a
model. This harness is the only thing standing between "we trust the extracted
metadata" and "we hope". The product decision was to trust extraction without
asking users to proofread it — that decision is only defensible with a number
behind it.

## The four verdicts

| Verdict | Truth | Extracted |
|---|---|---|
| `correct` | a value | the same value |
| `correct` | absent | absent |
| `wrong` | a value | a different value |
| `missed` | a value | absent |
| `hallucinated` | **absent** | **a value** |

`hallucinated` is the bucket that matters and the one a plain accuracy score
would hide: a `venue` populated for a paper that has none *looks* like a filled
field. Both dev-corpus papers are preprints with no year and no venue, so this
bucket is exercised on every run rather than being theoretical.

`missed` alone exits 0; any `wrong` or `hallucinated` exits 1.

## Comparison rules

- **Authors** are compared as a set — order is ignored, membership is not. A
  list missing one author is `wrong`, not partially correct, because that is
  what a user reading the answer gets.
- **Names are Unicode-normalised**: spacing modifiers are stripped, then NFKD,
  then combining marks, then casefold. `Evs¸en`, `Evşen`, and `Evsen` are the
  same person. PDF extraction genuinely produces the first form.
- **A blank string is absence**, not a value.

## Adding a case

Read the truth off page 1 of the paper itself — not off what the system
extracted, which is the thing being graded.

    {"paper_title_contains": "distinctive part of the title",
     "authors": ["Firstname Lastname", "..."],
     "year": null,
     "venue": null}

`null` and `[]` mean **the paper genuinely does not state this**. Getting that
wrong inverts the verdict: a real absence recorded as a value turns a correct
extraction into `missed`, and a real value recorded as `null` turns it into
`hallucinated`.

A case whose `paper_title_contains` matches no paper — or more than one — is a
fatal error, not a skip. A silently skipped case reads as a pass.

## The check this harness does NOT do

Extraction accuracy and the user-facing answer fail independently. Metadata can
be extracted perfectly while the chat still declines the question, if the
`PAPERS` block is injected but the system prompt steers the model toward
"the assigned papers do not appear to cover this".

Verify that separately, by hand: ask **"Who are the authors?"** in the UI and
confirm the answer names the right people, carries no `[n]` citation, and does
not decline.
