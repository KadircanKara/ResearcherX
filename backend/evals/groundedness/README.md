# Groundedness eval harness

Measures whether an ANSWER is carried by the excerpts it was given, and
whether its citation markers point at the papers that carry it. Measures only
— it never writes.

    docker compose exec -T backend python -m evals.groundedness.run_eval \
        --project-id <uuid> --per-case --json /tmp/groundedness.json

The `-m` form is required for the same reason every other harness documents:
`pyproject.toml` packages only `app*`, so `evals` is not installed and
file-path invocation fails with `ModuleNotFoundError: No module named 'app'`.

Flags: `--judge-model` (default `gpt-4.1`), `--concurrency` (default 1 — the
judge's TPM cap, not this, is the throughput bound; raising it mostly buys
429s the judge then sleeps off), `--set` (default: the retrieval harness's own
`golden_set.json`), `--limit N` for a smoke run, `--per-case`, `--json`.

## What this measures that `evals/retrieval` does not

`evals/retrieval` stops at *did the answering chunk reach the budget*.
Everything after that was unmeasured: whether the model used the chunk,
whether it asserted things the excerpts do not say, and whether the marker it
wrote points at the paper that supports the sentence.

`strip_misattributed_citations` already fixes one misattribution shape
deterministically — prose that NAMES a different paper. This harness catches
the shape that strip structurally cannot see: prose that names nobody and
cites a paper that does not say it.

## The evidence split is the point

A positive whose answering chunk never reached the budget is a **retrieval**
failure. Scoring its answer as a generation failure blames the wrong
subsystem and sends the next fix to the wrong place. So every headline number
is reported twice — pooled, and over the cases where the expected evidence was
actually in front of the model. `evidence_present` is computed with the golden
set's own `chunk_satisfies`, so "the evidence arrived" means exactly what "the
retrieval harness scored a hit" means; the two harnesses cannot drift into two
definitions of the same event.

## Metrics

Claims are **sentences** (`claims.py`). Groundedness is not a property of an
answer, it is a property of each assertion in it: four supported sentences and
one invented one is not "80% true", it is an answer with a hallucination in
it, and a whole-answer verdict cannot say which sentence to go read.

| metric | definition |
|---|---|
| `support` | supported / checkable claims, pooled over all claims |
| `halluc` | (unsupported + contradicted) / checkable |
| `contra` | contradicted / checkable — the model misreading evidence it *was* given, which is the half a better prompt can fix |
| `clean` | cases with **no** unsupported and no contradicted claim — the user-facing number |
| `cite-P` | correct markers / markers standing behind a checkable claim |
| `cite-cov` | supported claims carrying at least one marker |
| `abstention` | cases the assistant refused — **read per kind**: 1.00 is the target on negatives, a failure on positives |

`no_claim` sentences (headings, transitions, refusals, statements about what
the library contains) leave every denominator. Counting them as supported
would inflate groundedness by however much prose the model happened to write.

**Citation correctness is DERIVED, never judged.** The judge is asked one
question — does this excerpt set support this claim — and names the excerpts
that carry it; whether the marker the model wrote points at one of those
papers is then arithmetic (`metrics.ScoredClaim.correct_markers`). A model
asked to grade provenance and attribution in one pass lets the easy half
contaminate the hard half, and a derived answer can be recomputed from a
stored verdict when the policy changes, where a judged one needs the whole run
repeated.

**A marker on an unsupported claim is never correct**, whatever it points at:
there is no support for it to point to.

## The judge

Deliberately **not** on `app.llm.client.create_chat_completion`:

- The provider pool injects the active provider's model and rotates on quota
  exhaustion. A judge that silently changes model mid-run produces a column
  that is two measurements averaged together, with nothing in the output
  saying so — which destroys the only thing this harness is for, comparing
  runs.
- The dev answering model is `gpt-4.1-mini`. Letting it grade its own output
  is self-preference bias, and avoiding it costs nothing: the judge defaults
  to `gpt-4.1` on the same key.

### Rate limits — why the catalog is sliced

The catalog is up to `max_context_chunks` (60) chunks, ~26k tokens. A single
whole-catalog judge call exceeds this account's gpt-4.1 limit outright:

    429 ... Request too large for gpt-4.1 ... on tokens per min (TPM):
    Limit 30000, Requested 40051

So `judge.py` slices the catalog (`_pack`) and judges every claim against
**every** slice, merging the verdicts (`_merge`). Slicing is not sampling — no
excerpt is withheld from the judgement, only from any single request.
Precedence is `supported > contradicted > unsupported > no_claim`: each slice
answers "does anything HERE carry this claim", so one slice finding support
settles it. A claim both supported and contradicted across slices is a
disagreement *between papers*, not a hallucination — two papers reporting
different numbers is the normal state of a literature corpus.

The TPM cap also bounds throughput: ~60k tokens per case against 30k TPM is
roughly two minutes per case, so a full 42-case run takes well over an hour
regardless of `--concurrency`. Raise concurrency only if the account's limit
rises.

### A 429 means two different things

Both arrive as `RateLimitError` and they need opposite handling:

| body | meaning | handling |
|---|---|---|
| `Rate limit reached ... Limit 30000, Used 29575 ... try again in 24.216s` | throughput | sleep the hinted interval (+1s slack) and retry, up to 6 times |
| `You have no credits remaining` / `credit_balance_exhausted` | terminal | abort the WHOLE run |

The first real run (2026-08-22) treated them the same and paid for it: after
the account's credits ran out, the remaining 20 cases each spent a request to
produce an identical error row. `judge.QuotaExhausted` and the runner's
`abort` event exist so the first credit error stops everything still queued,
and the report says so instead of quietly reporting rates over whatever
finished. Answer generation goes through the PRODUCTION client, so it raises
the provider's own error rather than `QuotaExhausted` — the runner matches on
the text for that path.

The SDK's own backoff is not enough for the throughput case: it is shorter
than the window the server names, so its retries are spent before the TPM
minute rolls over.

A malformed judge response **raises**; the case is recorded in `ERRORS` and
excluded from every denominator. A fail-open judge would report a groundedness
number it never measured, which is worse than no number.

## Cost, and choosing a judge

Measured 2026-08-22 on this corpus (54.5 chunks/case, ~1762 chars/chunk, so a
~24k-token catalog per case):

| judge | judge cost, 42 cases | full run |
|---|---|---|
| `gpt-4.1` | $2.30 | $2.82 |
| `gpt-4.1-mini` | $0.46 | $0.98 |
| `gpt-4.1-nano` | $0.11 | $0.64 |

Answer generation is a fixed ~$0.52 of that, so **the judge is ~82% of the
bill** and the catalog is nearly all of the judge's input.

Whether a cheaper judge suffices is an EMPIRICAL question, and the harness
answers it about itself rather than assuming:

    # once: the expensive reference run, saving its answers
    ... run_eval --project-id <uuid> --judge-model gpt-4.1 \
        --save-generations /tmp/generations.json --json /tmp/reference.json

    # then, for judge tokens only, on byte-identical input
    ... run_eval --project-id <uuid> --replay /tmp/generations.json \
        --judge-model gpt-4.1 --compare-judge gpt-4.1-mini

`--replay` matters for more than the money: two judges run against freshly
generated answers are grading different text, because generation is sampled,
and a disagreement would be inseparable from the models having answered
differently. The cache refuses a version mismatch rather than parsing
best-effort — a replay judging against a catalog that is not the one the model
saw would be wrong in a way nothing reports.

Read the agreement block in this order:

1. **MISSED problems** (`false_clean`) — claims the reference called a problem
   and the candidate called fine. Every one is a hallucination the cheaper
   judge would let through. This is the number that decides it; false alarms
   only cost an engineer some reading.
2. **kappa, with `claims compared`** — never raw agreement alone. These
   verdicts are heavily skewed toward `supported`, so a judge that answered
   "supported" to everything would score ~0.95 raw while being worth nothing.
   Kappa is what exposes that; `test_kappa_exposes_a_judge_that_stopped_
   discriminating` pins the case.
3. **the confusion matrix**, for whether disagreements are a real split or one
   label leaking into another.

`unsupported` and `contradicted` are the same side of the shippability line, so
a candidate swapping one for the other disagrees on detail, not on decisions —
that is what the `on problem/not-a-problem` column separates out.

## How answers are produced

`generate.py` assembles production's own components rather than calling
`ChatService.respond`. `respond` would guarantee zero drift, but it persists an
assistant message and needs a conversation row, and every other harness here
reads only — leaving 42 throwaway conversations in the dev project (or half of
them, after a crash) is the worse trade. It also cannot supply what the judge
needs: the `done` event carries 200-character snippets of CITED chunks only,
where the judge must see the whole catalog.

Every **decision** is imported and never reproduced: retrieval
(`_retrieve_paper_chunks` / `_retrieve_mentioned_chunks`), scope resolution,
the widener, `needs_paper_metadata`, the system prompt and streaming, and the
citation post-pass in production's load-bearing order. What is repeated is the
**wiring**. That is a real drift surface, and
`tests/test_evals_groundedness_parity.py` pins it — post-pass order against
production's own source, retrieval imported rather than mirrored, and that the
module writes nothing.

Not reproduced, because the golden set is single-turn: conversation-history
retrieval, query reformulation (production skips it on a first turn anyway),
persistence, and SSE emission.

## Reading the output

- **`ev`** in the per-case table: did the golden set's expected text reach the
  model. `NO` means read that row as a retrieval result, not a generation one.
- **`markers shown but unjudgeable`**: markers inside sentences too short to be
  claims (`claims._MIN_CLAIM_CHARS`). They have no claim to be right about, so
  they are outside `cite-P`'s denominator — reported so the gap is visible
  rather than silently absent.
- **`markers removed by strip_misattributed_citations`**: production's
  deterministic strip fires *before* scoring, so `cite-P` measures what the
  reader would actually see, not what the model first wrote.

Results are model-specific twice over — the answering model AND the judge.
Both are printed in the header; re-baseline when either changes.
