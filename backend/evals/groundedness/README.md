# Groundedness eval harness

Measures whether an ANSWER is carried by the excerpts it was given, and
whether its citation markers point at the papers that carry it. Measures only
— it never writes.

    docker compose exec -T backend python -m evals.groundedness.run_eval \
        --project-id <uuid> --per-case --json /tmp/groundedness.json

The `-m` form is required for the same reason every other harness documents:
`pyproject.toml` packages only `app*`, so `evals` is not installed and
file-path invocation fails with `ModuleNotFoundError: No module named 'app'`.

Flags: `--judge-model` (default `gpt-4.1-mini` since 2026-09-24; `gpt-4.1` before —
see *Measured — 2026-08-22d*), `--concurrency` (default 1 — the
judge's TPM cap, not this, is the throughput bound; raising it mostly buys
429s the judge then sleeps off), `--set` (default: the retrieval harness's own
`golden_set.json`), `--limit N` for a smoke run, `--per-case`, `--json`,
`--metrics` / `--csv` / `--allow-self-judge` (see *Judged relevance* below).

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
- Letting the answering model grade its own output is self-preference bias;
  the runner refuses it unless `--allow-self-judge`. The judge defaults to
  `gpt-4.1-mini` (it was `gpt-4.1` until 2026-09-24): mini matched gpt-4.1 on
  every aggregate over identical answers (2026-08-22d below) at a fifth of the
  price. Re-confirm a decision that hinges on hallucination counts with
  `--judge-model gpt-4.1`.

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

## Measured — 2026-08-22 (first reference run)

- corpus 4594 chunks / 102 papers (project `fa2ab869…52922`), the same project
  every `evals/retrieval` block uses
- answering model `gpt-4.1-mini`, judge `gpt-4.1`, 40 of 42 cases scored
- cost ~$2.80; the two lost cases are recorded below

| group | cases | claims | support | halluc | undisclosed | disclosed | clean | cite-P | cite-cov |
|---|---|---|---|---|---|---|---|---|---|
| positives (pooled) | 29 | 159 | 0.99 | 0.01 | **0.01** | 0.00 | 0.93 | 0.96 | 0.39 |
| evidence reached the model | 27 | 141 | 0.99 | 0.01 | 0.01 | 0.00 | 0.93 | 0.96 | 0.40 |
| evidence did NOT reach | 2 | 18 | 1.00 | 0.00 | 0.00 | 0.00 | 1.00 | 1.00 | 0.28 |
| off_topic negatives | 11 | 39 | 0.36 | 0.64 | **0.00** | 0.72 | 0.18 | — | 0.00 |

**Generation is not the weak link.** Two ungrounded claims in 159, no
contradictions anywhere, citation precision 0.96. Both failures are `figure`
cases -- `harvested-power-duration-figure` ("these observations are summarized
in excerpts [1]..[5], which include related figures") and
`brkga-convergence-figure` -- which is a coherent finding rather than two
one-offs: a figure question retrieves the caption's surrounding TEXT, and the
model then describes what the figure shows, which the text does not state.

**The negatives' 0.64 is the prompt, not the model.** Undisclosed
hallucination on the off_topic set is **0.00**: every ungrounded claim stands
after the hand-off production's own SYSTEM prompt instructs. What the split
exposes instead is `disclosed = 0.72` and `abstention = 0.36` -- on 7 of 11
off-topic questions the assistant declined and then answered anyway, with
uncited FAA certification requirements, LiPo chemistry and specific FPV
product recommendations. That is a product decision to revisit, not a bug to
fix: the model is obeying its instructions exactly.

**Citation coverage is the real gap: 0.39.** Six of every ten SUPPORTED claims
carry no marker at all. The pattern is consistent across cases -- the model
writes uncited bullets and then dumps every marker into one trailing sentence
("These observations are summarized in excerpts [1], [2], [3], [4], [5]"),
which is also how the worst positive failure happened. The claims are
grounded; the reader simply cannot tell which paper any one of them came from.

**Two cases lost to the judge**, both with the same signature -- verdicts for
the first two claims and then nothing: `frontier-mesh-clustering` and
`offtopic-cake`. The judge now retries the missing indices once before failing
a case.

The `evidence did NOT reach` row is 2 cases here against the retrieval
harness's 4 blocked positives, because this harness runs the shipped hybrid
path with the per-paper candidate guarantee, and `evals/retrieval`'s
closed-form section reports the dense-arm figure.

### Measured — 2026-08-22b (after the prompt changes)

Same corpus, same judge (`gpt-4.1`), same answering model (`gpt-4.1-mini`),
same golden set. Three prompt changes were in effect: no general knowledge,
a marker in the sentence that makes the claim, and (NOT in this run -- it
landed after the process started) the figure rule.

| group | cases | claims | support | halluc | undisclosed | disclosed | clean | cite-P | cite-cov |
|---|---|---|---|---|---|---|---|---|---|
| positives (pooled) | 30 | 149 | 0.99 | 0.01 | 0.01 | 0.00 | 0.93 | 0.96 | **0.59** |
| evidence reached the model | 28 | 137 | 0.99 | 0.01 | 0.01 | 0.00 | 0.93 | 0.96 | **0.62** |
| evidence did NOT reach | 2 | 12 | 1.00 | 0.00 | 0.00 | 0.00 | 1.00 | 1.00 | 0.17 |
| off_topic negatives | 12 | 12 | **1.00** | **0.00** | 0.00 | **0.00** | **1.00** | — | 0.00 |

Against the reference run:

| | reference | after | |
|---|---|---|---|
| negatives, abstention | 0.36 | **1.00** | every off-topic question now answered "The ingested documents do not cover this." |
| negatives, disclosed | 0.72 | **0.00** | the hand-off is gone from the prompt, and the model stopped doing it |
| negatives, clean | 0.18 | **1.00** | |
| positives, cite-cov | 0.39 | **0.59** | +0.20; on the evidence-reached rows 0.40 -> 0.62 |
| positives, abstention | 0.00 | 0.00 | the refusal rule did NOT leak into questions the corpus answers |
| positives, clean | 0.93 | 0.93 | unchanged -- see below |
| cases scored | 40/42 | **42/42** | the judge's missing-index retry recovered both lost cases |

**The refusal did not over-fire.** The risk of a blanket no-fallback rule is
that it starts declining questions the corpus does answer; positive abstention
stayed at 0.00, so it did not.

**Citation coverage improved but is not solved at 0.59.** Four of ten supported
claims still carry no marker. The trailing-summary anti-pattern the rule names
did stop -- `handover-figure` went 1/5 to 5/5 markers, `harvested-power-
duration-figure` 1/7 to 6/7.

**Hallucination on positives did not move, and the failures MOVED.** Both
reference failures (`harvested-power-duration-figure`, `brkga-convergence-
figure`) are clean; two different cases now fail (`ruav-cluster-count`,
`nemo-mobility`), one claim each. At n=2 out of ~150 claims this is not
distinguishable from sampling noise in either direction: do NOT read it as
"the figure problem is fixed" (the figure rule was not even in this run) or as
a regression. It is the same rate with different cases behind it, which is
what a two-event denominator looks like.

### Measured — 2026-08-22c (adding the figure rule made things worse)

Run 3 differs from run 2 by ONE prompt change: the figures-are-not-visible
rule. Same corpus, judge, answering model and golden set. Numbers below are
rescored with the current claims policy (one list-introducer artifact dropped,
see "the harness was wrong too").

| metric (positives) | run 2 | run 3 |
|---|---|---|
| support | 0.99 | **0.96** |
| hallucinated | 0.01 | **0.04** |
| clean | 0.93 | **0.87** |
| citation precision | 0.96 | **0.91** |
| citation coverage | 0.59 | **0.43** |

Every positive metric moved the wrong way, and **the rule did not even fix the
thing it was written for**: `mogoa-framework-figure` was clean in run 2 and in
run 3 produced exactly the failure the rule forbids -- "this visualization
outlines the flow and interaction between different population components",
which no sentence states.

Worse, it partly UNDID run 2's gain: `harvested-power-duration-figure` went
from 6 of 7 supported claims carrying a marker to 1 of 5.

The most likely explanation is prompt dilution -- the citation rule and the
figure rule compete, and the figure paragraph is long. This codebase already
carries an ORDER IS DELIBERATE warning on the metadata block for a
live-verified version of the same effect. A shorter rule placed adjacent to
the citation rules is worth trying; the paragraph as written is not.

**Do not read run 3 as proof that prompt rules cannot fix figures.** One run,
five figure cases, two events. What it does establish is that THIS rule, at
THIS length, in THIS position, costs more than it buys.

**The rule was reverted the same day**, so the shipped prompt is run 2's.
`tests/test_chat_agents.py::test_the_prompt_carries_no_figure_paragraph` pins
the absence and points here, so it cannot be re-added without re-measuring.
Figure over-claiming stays a known ~1-in-150-claims failure; the next attempt
is retrieval-side -- anchor a figure's caption to the paragraphs that discuss
it -- rather than more prompt text.

### The harness was wrong too

Run 3 surfaced a bug in `claims.extract_claims`: a list introducer ("The
process involves:" followed by a numbered list) was extracted as a claim and
judged unsupported -- a harness artifact counted against the model. A colon is
not a sentence terminator, so the splitter also carried the list's first marker
onto the introducer ("...involves:\n\n1."), which is why the fix strips a
trailing enumerator ON ITS OWN LINE before testing for the colon; an unanchored
rule would eat the "12." out of "The measured value is 12."

`--rescore` now aligns stored verdicts to re-extracted claims BY TEXT, and the
`--json` dump carries each claim's text so it can. Index alignment -- the only
option for dumps written before this -- cannot drop a claim that extraction no
longer produces, so it keeps counting exactly the artifact a claims-policy fix
was meant to remove. A rescore of an older dump says so in its header.

### Measured — 2026-08-22d (is gpt-4.1-mini good enough as the judge?)

Both judges graded the SAME stored generations (run 2's), so the comparison is
byte-identical input. Only mini had to be paid for -- $0.46 against the $2.30
a fresh reference run would have cost.

| | gpt-4.1 | gpt-4.1-mini |
|---|---|---|
| positives: support / halluc / clean | 0.99 / 0.01 / 0.93 | 0.99 / 0.01 / 0.93 |
| positives: cite-P / cite-cov | 0.96 / 0.59 | 0.96 / 0.59 |
| agreement (149 claims) | — | raw **0.99**, kappa **0.49** |
| disagreements | — | 1 missed problem, 1 false alarm |

Every reported aggregate is identical. The two per-claim disagreements are
`nemo-mobility` #2 (mini called a problem fine) and `occupancy-updates` #6
(mini flagged a claim gpt-4.1 supported).

**Verdict: mini is adequate for the aggregate metrics, and the evidence about
problem-DETECTION is two events.** Missing 1 of the reference's 2 problems is a
50% miss rate on the number that matters, and it is also n=2 -- neither
"adequate" nor "unsafe" is established. Use mini for iteration; re-confirm a
decision that hinges on hallucination counts with gpt-4.1. Kappa 0.49 says the
same thing from the other side: raw 0.99 looks conclusive only because the
population is overwhelmingly `supported`.

**The measurement's first result was a harness bug, not a judge difference.**
Before the fix below, agreement read raw 0.91 / kappa 0.11 with 13
disagreements -- and 12 of those 13 were one sentence: production's refusal,
"The ingested documents do not cover this." gpt-4.1 labelled it `supported` on
all 12 negatives; mini labelled it `unsupported` on all 12. Neither used
`no_claim`, though the judge prompt lists refusals there -- the sentence reads
as a checkable statement ABOUT the corpus, so neither model was being
unreasonable.

The fix is deterministic, not a sharper judge prompt: the refusal string is
fixed and known, so `claims.REFUSAL` excludes it before any judge sees it, and
a parity test pins it against `SYSTEM`. A refusal is a behaviour to measure,
not a claim to grade -- so a refusing case now has zero checkable claims, its
support/hallucination columns read "-", and `abstention_rate` carries the whole
signal. Agreement reporting filters stored verdicts through the CURRENT claims
policy for the same reason: comparing verdicts about text that is no longer a
claim blames the models for a harness decision.

### Measured — 2026-08-22e (gpt-4.1-nano is not usable as a judge)

Same stored generations again, so the input is byte-identical to both runs
above. Cost $0.11.

| | gpt-4.1 | gpt-4.1-mini | gpt-4.1-nano |
|---|---|---|---|
| agreement with the reference | — | raw 0.99, **kappa 0.49** | raw 0.93, **kappa -0.02** |
| missed problems / false alarms | — | 1 / 1 | **2 / 8** |
| cases scored | 42 | 42 | **40** |
| positives: support / clean | 0.99 / 0.93 | 0.99 / 0.93 | 0.94 / **0.75** |

**Kappa -0.02 is the whole verdict: nano agrees with the reference no better
than chance would.** It missed BOTH problems gpt-4.1 found and invented eight
that are not there.

The failure mode is not the one predicted. A cheap judge was expected to
rubber-stamp -- to answer `supported` to everything and score high raw
agreement on a population that is 99% supported. Nano instead judges NOISILY:
it is stricter than the reference on balance, so its numbers look like a
harsher grader rather than a broken one.

**That is what makes it dangerous, and what the agreement check is for.**
Nano's standalone report is entirely plausible -- support 0.94, clean 0.75,
citation precision 0.92 -- and a reader without a reference would accept it as
a slightly stricter measurement. Only the per-claim comparison shows there is
no signal in it. Raw agreement 0.93 would have been reassuring on its own too;
kappa is what exposes it.

It also cannot hold the output contract. Two cases failed outright, one with:

    ValidationError: 1 validation error for JudgeResponse
    verdicts: Input should be a valid array
    [type=list_type, input_value={'0': 'unsupported', '1': ...}]

-- it returned an object keyed by claim index where the schema demands an
array, after the schema was pasted into its prompt and after the missing-index
retry. Silently accepting that shape would be easy and wrong: a judge that
cannot follow the output contract is not a judge whose verdicts should be
trusted.

**Use gpt-4.1-mini for iteration and gpt-4.1 for decisions. Do not use nano.**

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

**The wiring drifted once already, and nothing crashed.** When the harness was
brought onto structured chunking (2026-09-20) it was still calling both
retrieval methods without `reranker=`, so it graded the fused catalog order
while production served the reranked one, and it rebuilt each chunk field by
field, dropping the `section` and `page` the model's excerpt header carries.
Both are now pinned by the parity tests, and the generation cache is version 2
(it stores section and page) — a version-1 cache is refused, not upgraded.

## Judged relevance (`--metrics`)

Groundedness is silent on two other ways a turn fails, so two judged metrics
sit beside it. Both are **opt-in**: the default run is still the support-only
run every "Measured" block above was taken with.

    --metrics support,answer_relevance,context_relevance

| metric | the judge sees | reported as |
|---|---|---|
| `answer_relevance` | question + answer, **never the excerpts** | mean 0–1 (`answer-rel`) |
| `context_relevance` | question + excerpts, **never the answer** | `ctx-P`, `ctx-P@10` |

- **Answer relevance** catches the fully grounded answer to a question nobody
  asked. It is not shown the catalog, because whether the answer is *supported*
  is the support judge's question and a well-grounded non-answer would
  otherwise score high on the wrong evidence. It is **not scored on
  `off_topic`**: the right answer there is a refusal, which the rubric scores
  0.0 by design, and averaging a column of correct refusals in as zeros would
  make refusing look like failing. `stance` grades those cases.
- **Context precision** is excerpts a person answering would use, over
  excerpts *shown* — the noise the model had to read past, which
  `evals/retrieval`'s recall cannot see. The judge is never shown the answer:
  one that can see it marks an excerpt relevant because the answer used it,
  and the column becomes a second, worse citation metric. `ctx-P@10` cuts by
  catalog **position**, not by excerpt number — numbers are post-renumbering
  citation numbers, and `[7]` can stand first. It **does** run on `off_topic`,
  where it should read ~0.00; anything above is what the distance gate let
  through. Sliced like the support judge, and like it refuses to fail open: an
  excerpt that comes back with no verdict is an error, never "not relevant".

A relevance call that fails costs that case its relevance columns **only**. It
is listed under ERRORS with stage `relevance`, and the support outcome — already
paid for — stays in every groundedness denominator.

`--csv PATH` writes one row per case: question, answer, the contexts as a JSON
list, and every score. An unmeasured score is an **empty cell, never 0** — a
zero is a measurement.

### Measured — 2026-09-24 (first full relevance run)

51 cases (39 positives, 12 `off_topic`), answering `gpt-5-mini` at
`LLM_REASONING_EFFORT=low` with `CHAT_ANSWER_MAX_TOKENS=6000`, judge
`gpt-4.1-mini` (the new default), 60-chunk budget, hybrid retrieval + Cohere
rerank, `--metrics support,answer_relevance,context_relevance`, exported with
`--langfuse` (experiment `758c82ffee74076a`).

| group | cases | claims | support | halluc | clean | cite-P | cite-cov | answer-rel | ctx-P | ctx-P@10 |
|---|---|---|---|---|---|---|---|---|---|---|
| positives (pooled) | 39 | 111 | 0.98 | 0.02 | 0.95 | 0.96 | 0.94 | 0.97 | 0.14 | 0.35 |
| evidence reached | 35 | 98 | 0.99 | 0.01 | 0.97 | 0.97 | 0.93 | 0.96 | 0.15 | 0.38 |
| evidence did NOT reach | 4 | 13 | 0.92 | 0.08 | 0.75 | 0.92 | 1.00 | 1.00 | 0.07 | 0.10 |
| `off_topic` | 12 | 0 | — | — | 1.00 | — | — | — | 0.01 | 0.01 |

By kind (positives): `content` (25) ctx-P 0.17 / @10 0.38, `figure` (13) 0.09 /
0.32, `metadata` (1) 0.08 / 0.20. Abstention 0.03 on positives, 1.00 on
negatives; contradicted claims 0.00. Two cases (`ground-control-station`,
`fig-arc-distance-steering`) lost their context-relevance columns to a judge
response missing excerpt verdicts; their support outcomes are counted.

Tokens: `gpt-4.1-mini` 299 calls, 2.11M in / 67k out; `gpt-5-mini` 54 calls,
1.31M in / 16k out (8.8k reasoning); Cohere 94 search units. About **$1.50**
at list prices: judge ~$0.95, answers ~$0.36, rerank ~$0.19. Context relevance
re-reads every catalog, so it roughly doubles the judge's input against a
support-only run.

**Reading.** The answers hold up; the context does not. Support and answer
relevance are both near ceiling, but only **14%** of the excerpts the model is
shown are ones a person answering would use — **35%** of the first ten. The
2026-09-20 smoke reading's shape (below) holds at full size: the rerank puts
the useful excerpts first, and most of the 60-chunk budget is read past.
`off_topic` questions still carry ~45 excerpts each through the distance gate
(542 shown, ctx-P 0.01); the prompt's refusal rule, not retrieval, is what
keeps them clean. That makes a smaller budget, or a relevance cutoff after the
rerank, the next thing worth measuring — this run does not measure it, and a
cut that trims noise also risks the 4 positives where evidence already failed
to arrive.

Caveats: one judge, from the answering model's family (the runner prints a
same-family NOTE); not confirmed against `gpt-4.1`. Relevance verdicts are
`gpt-4.1-mini`'s, and the 2026-08-22d judge comparison covered support only.

### Judging with the Claude Code CLI

`tools/claude-proxy` already turns `claude -p` into an OpenAI-compatible
endpoint, and the judge owns a plain `AsyncOpenAI` client pointed at
`JUDGE_BASE_URL` — so a `claude -p` judge is an env override, not a second
transport:

    make claude-proxy                      # on the HOST; serves answerer and judge

    JUDGE="-e JUDGE_BASE_URL=http://host.docker.internal:8787/v1 -e JUDGE_API_KEY=claude-code"

    # 1. answer once (the answering model is whatever LLM_MODEL names)
    docker compose exec -T $JUDGE backend python -m evals.groundedness.run_eval \
        --project-id <uuid> --judge-model sonnet --save-generations /tmp/gen.json

    # 2. judge the SAME answers, as often as needed
    docker compose exec -T $JUDGE backend python -m evals.groundedness.run_eval \
        --project-id <uuid> --replay /tmp/gen.json --judge-model sonnet \
        --metrics support,answer_relevance,context_relevance \
        --csv /tmp/claude_cli_eval_results.csv --json /tmp/groundedness.json

The `-e` overrides are needed whenever `.env` points `JUDGE_BASE_URL` at
another vendor, and on BOTH steps: there is no generate-only mode, step 1
judges too, and a case whose judge call fails is an error row whose generation
is **not** saved. `/tmp` is the container's; `docker compose cp
backend:/tmp/claude_cli_eval_results.csv .` brings a file out.

**The judge must not be the answerer.** With the proxy serving both, that is
one flag away, so the runner refuses `--judge-model X` when the answering model
is `X` — *before* any call is made — unless `--allow-self-judge` is passed, in
which case the header says `SELF-JUDGED`. Two models from one family (sonnet
grading opus) run, with a same-family note in the header: self-preference is
reduced by a different model, not removed. For a number a decision rests on,
confirm a Claude-judged run against a `gpt-4.1` judge over the same `--replay`
cache with `--compare-judge` / `--agreement-against`.

Keep `--concurrency 1` with the proxy unless its own `--concurrency` is raised
to match: both ends draw on one CLI login, and the proxy's semaphore is the
real bound.

Smoke run, 2026-09-20, 2 content cases, sonnet judging opus, 60-chunk
catalogs: support 0.95, answer-rel 0.88, **ctx-P 0.22, ctx-P@10 0.75**. Two
cases is a wiring check, not a measurement — but the shape is the expected one:
the rerank puts the useful excerpts first, and most of a 60-chunk budget is
read past.

## Disclosed general knowledge is not hallucination

Production's own system prompt (`app/agents/chat_agent.py::SYSTEM`) instructs
the hand-off:

    "If the answer cannot be found in the excerpts or the PAPERS block, say:
     'The assigned papers do not appear to cover this. Based on general
      knowledge: ...'"

Everything after that sentence is unsupported by construction. Pooling it into
the hallucination rate reports the prompt working exactly as written as a
defect -- on the 2026-08-22 reference run it turned an 0.00 undisclosed
hallucination rate on the negatives into an alarming 0.64.

So claims standing after a hand-off are marked `disclosed` and counted apart:

- **`undisclosed`** is the hallucination number -- ungrounded claims with no
  disclaimer in front of them.
- **`disclosed`** is the exposure that number does not cover, and it is
  reported next to it precisely so the split cannot be used to hide anything.
  A disclosed claim is still ungrounded text a reader can mistake for grounded
  text: one disclaiming sentence followed by six confident sentences about FAA
  certification requirements is exactly how it reads on screen.

`claims._HANDOFF_MARKERS` is pinned against the production prompt by
`tests/test_evals_groundedness_parity.py`. If that wording changes and the
marker list does not, every general-knowledge answer silently becomes a
hallucination.

## Rescoring without re-judging

A SCORING-POLICY change is not a measurement change. The disclosed/undisclosed
split was added *after* the reference run had already spent $2.30 of judge
tokens, and every verdict it needed was already in that run's `--json` dump:

    ... run_eval --project-id <uuid> --rescore /tmp/groundedness_ref.json

No model calls. `claims.extract_claims` is deterministic over the stored
answer, so claim flags re-derive exactly; judge verdicts are read back as
recorded. Re-judging instead would have re-bought the same answers with fresh
sampling noise on top, and any change in the numbers would have been
inseparable from the policy change being measured.

## Exporting a run to Langfuse (`--langfuse`)

    ... run_eval --project-id <uuid> --judge-model gpt-4.1-mini \
        --langfuse --langfuse-run "gpt-5-mini low, judged by gpt-4.1-mini"

Opt-in, and it needs `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`. A run then
shows up in Langfuse as an **experiment** on the dataset
`groundedness-golden-set`, so two runs can be compared side by side (Datasets
→ groundedness-golden-set):

- **the dataset** is the golden set, one item per case (`groundedness-<case
  id>`), upserted at the start of every exporting run. `--limit N` uploads only
  those N.
- **one trace per case**, `eval.case`, with the case's embedding, retrieval,
  rerank, answer and judge calls nested under it -- the same spans a chat turn
  produces, so latency and cost read the way they do for `chat.turn`. Traces
  land in the environment `sdk-experiment`, which keeps them out of the dev
  chat numbers.
- **per-case scores** on each trace: `support`, `hallucination`,
  `citation_precision`, `citation_coverage`, `claims`, `clean`, `refused`,
  `evidence_present`, plus `answer_relevance` / `context_precision` /
  `context_precision_at_10` when `--metrics` asked for them. A rate a case has
  no denominator for (a refusal has no checkable claim) is not posted.

**The report stays the source of truth for headline numbers.** It pools
support over claims and splits it by evidence; Langfuse averages per-case
values, which weights a two-claim answer like a fifteen-claim one. Use Langfuse
for drill-down and run-to-run comparison, and record measurements from the
report.

The experiment is recorded through `langfuse.experiment.*` span attributes over
OTLP, not `POST /api/public/dataset-run-items`, which Langfuse Cloud removes on
2026-11-16. Dataset setup runs BEFORE any model call and aborts the run if it
fails; a score that fails to post is counted in the output, never raised.
Scores go through the batched `/api/public/ingestion` endpoint as
`score-create` events, not one `POST /api/public/scores` each: that endpoint
is in Langfuse's general bucket, 30 requests a minute on Hobby, and the first
full export (2026-09-24) lost 449 of 478 scores to 429s. Every REST call also
retries a 429 after the `retryAfterSeconds` the server names. Score ids are
`<experiment id>-<case id>-<name>`, so re-sending a run's scores overwrites
rather than duplicates, and `--json` records the experiment id and each case's
trace so a failed export can be re-sent from the dump.
With `LANGFUSE_CAPTURE_CONTENT` on (the default) each trace carries the
question, the excerpt catalog and the answer. A `--replay` run has no
answering calls, so it shows judge cost and latency only.

Units: a 2-case smoke run on 2026-09-24 sent 19 observations, 2 traces and 16
scores; the full 51-case run with relevance sent 51 traces and 478 scores,
comfortably inside the Hobby plan's 50k a month.

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
