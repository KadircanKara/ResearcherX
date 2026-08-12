# Retrieval eval harness

Measures retrieval quality against the **live dev database**. Measures only —
it never writes.

> **2026-08-10 — numbers recorded before this date are not comparable.** The
> harness simulated top-k *per paper* until production dropped per-paper
> allocation; `--k` is now a global budget defaulting to `max_context_chunks`.
> A recall@5 from the old harness and a recall@5 from this one measure
> different things.

    docker compose exec -T backend python -m evals.retrieval.run_eval --project-id <uuid>
    docker compose exec -T backend python -m evals.retrieval.run_eval --project-id <uuid> --k 3 --json /tmp/retrieval.json

The `-m` form is required: `pyproject.toml` packages only `app*`, so `evals`
isn't installed and running it by file path fails with
`ModuleNotFoundError: No module named 'app'`.

Flags:

- `--k` — total chunks retrieved globally (default: `max_context_chunks`,
  production's own budget). See "What it measures vs. what production does"
  below for how this harness's query still differs from production's.
- `--project-id` — **required.** Scopes the corpus to one project's papers,
  matching how production always scopes retrieval. There is no unscoped path
  in production, so there is no default here: a run across every project would
  report numbers production cannot produce.
- `--set` — path to an alternate golden-set JSON file (default: `golden_set.json`
  next to this file, same schema as "Adding a case" below).
- `--json` — also write the full per-case results, threshold-sweep grid, and
  closed-form separation to this path. See "Reading the output" for the
  `--json` shape and how it differs from the console report.
- `--targeted` — also measure production's **single-paper** cut (see
  "Targeted mode" below). Costs no extra queries: it re-uses the chunks
  already fetched for the global report. Absent, `main()`'s existing global
  report is unchanged.

## What it measures vs. what production does

The runner's SQL mirrors production's **distance computation** (same `<=>`
cosine operator, same `WHERE model = :model` filter) and, since 2026-08-10,
its **top-k too**: production applies no per-paper ceiling, just
`ORDER BY distance ASC LIMIT max_context_chunks`, which is exactly what
`--k` (default: `max_context_chunks`) simulates here.

Project scoping matches: production scopes retrieval to the current project's
papers (`chat_service._retrieve_paper_chunks` joins on a `scope` CTE built from
that project's paper ids), and `--project-id` is required here for the same
reason. Under the old per-paper top-k an unscoped harness was harmless, since
another project's chunks could not take a slot away from the paper being
measured; under a global top-k they compete for the same fixed budget directly.

One divergence remains:

- **Query reformulation.** Production (`chat_service.py`) reformulates the
  query through a `QueryReformulatorAgent` only when the conversation has
  prior turns — a first turn is already standalone, so the call is skipped
  and the raw question is embedded directly. This harness always embeds the
  golden-set `question` verbatim. Since the golden set is single-turn
  questions, the two paths still agree: there's no reformulation for either
  of them to diverge on. Re-validate this section if the golden set ever
  grows multi-turn cases, or after any change to the reformulator's behavior.

## Targeted mode

    docker compose exec -T backend python -m evals.retrieval.run_eval \
      --project-id <uuid> --targeted

The global report above measures the path production takes when retrieval is
scoped to the whole project. Since 2026-08-10 there is a second production
path: when a paper targeter has scoped retrieval to ONE paper,
`chat_service._retrieve_paper_chunks` swaps in a looser SQL ceiling
(`settings.intra_paper_ceiling`) plus a relative delta cut
(`settings.intra_paper_delta`, via `keep_within_paper`) instead of the global
`similarity_threshold`. `--targeted` is what measures *that* path — the
global report cannot see it at all, because a global top-k across 100 papers
never lets one paper's low-ranked-but-correct chunk through.

**What it simulates.** Each case is scoped to a single paper, the same way
the targeter would:
- `content` / `metadata` / `figure` cases scope to the paper the case names
  (`_scope_to_paper`) — the targeter picked correctly.
- `off_topic` cases scope to whichever paper holds the globally nearest
  chunk (`_scope_to_nearest_paper`) — this simulates the targeter
  *mis-firing* on a question the library cannot answer, which is exactly the
  scenario `intra_paper_ceiling` exists to contain. There is no "correct"
  paper to scope an off_topic case to, so the worst case (nearest wrong
  paper) is what's measured.

**A `paper_title_contains` needle must be unique.** Production's
single-paper policy (`single_paper = len(paper_infos) == 1` in
`chat_service._retrieve_paper_chunks`) assumes there really is only one
paper in scope — the delta cut is relative to that one paper's own nearest
chunk. If a case's `paper_title_contains` substring matches two or more
papers, `_scope_to_paper` would otherwise blend a second paper's chunks
into the scope, describing a measurement production can never actually
produce. `_targeted_case_status` catches this before it can happen silently:
an ambiguous case is routed to the existing `errors` list (same channel as
"no paper matching ..." in the global report) instead of into
`targeted_rows`, and prints under the `ERRORS (golden-set problems...)`
block naming how many distinct papers it hit. Fix is the same one "Adding a
case" below already asks for: pick a more distinctive substring.

`_production_cut` then applies the cut to that scoped, distance-sorted list
in production's exact order — SQL filters on the ceiling, LIMITs to
`max_context_chunks`, *then* the delta cut runs in Python. The order does
not actually change the result today: ceiling, budget and delta are all
prefix truncations of the same distance-ascending sort, and the delta cut
point depends only on `distances[0]` (the nearest chunk), which no earlier
truncation can move — any ordering yields the same
`min(n_ceiling, n_delta, budget)`. The order is still mirrored deliberately:
if either side ever adds a non-prefix operation (MMR, dedup, a rerank), a
harness that copies production's literal order will surface the divergence
immediately instead of hiding behind today's accidental equivalence. The cut
itself is `keep_within_paper`, **imported** from
`app.services.intra_paper_ranker` rather than reimplemented here, so the
harness can never measure a policy that has drifted from what production
actually ships.

**The metric is survival@cut**: of the positive cases, what fraction still
have their satisfying chunk after the single-paper cut runs
(`kept` chunks), not just somewhere in the paper's full chunk list
(`intra_rank`). A case can show a deep `intra_rank` and still `survive` —
that's the point of the looser ceiling — or show a shallow one and still get
cut, which is a real finding, not a bug to code around.

The report's columns:

- **chunks** — how many chunks the scoped paper contributed (its full
  chunk count for positives; the nearest-paper's chunk count for
  off_topic).
- **intra_rank** — the satisfying chunk's 1-based rank *within that single
  paper's* distance order, before any cut. `-` for off_topic (no satisfying
  chunk is defined for a negative).
- **kept** — how many chunks survive `_production_cut`. For off_topic rows
  this is the ceiling measurement, not a rank: it is what
  `intra_paper_ceiling` lets through when the targeter picks the wrong paper.
- **survived** — whether the satisfying chunk is still present after the
  cut. `-` for off_topic, same reason as `intra_rank`.

**`kept = 0` on an off_topic row does not mean production returns nothing.**
It means the single-paper cut emptied the mis-targeted paper's scope — but
`chat_service.py`'s `respond()` re-queries the untargeted (whole-project)
scope whenever the targeted retrieval comes back empty
(`scope is not paper_infos and not paper_chunks`, right after
`_retrieve_paper_chunks` in `respond()`), so production still answers, from
global chunks, instead of returning nothing. This matters for the "Open
finding" below: raising `intra_paper_ceiling`'s scrutiny would push more
mis-targeted off_topic questions toward `kept = 0`, i.e. toward *this*
fallback rather than toward an empty, ungrounded answer — the two are not
the same failure mode, and a tighter ceiling trades one for the other rather
than eliminating a risk outright.

`survival@cut` at the bottom is `survived == yes` count over scored
(non-off_topic) cases — the single number to watch when re-tuning
`intra_paper_delta`. The `ceiling check` line beneath it is the off_topic
side of the same coin: the worst (largest) `kept` count across off_topic
rows, flagged if it exceeds 2 — a large kept-count on a mis-targeted
off_topic case means the ceiling itself is too loose, independent of delta.

In `--json`, this is the `targeted` key (a list of the same per-case rows,
or `null` when `--targeted` wasn't passed).

### Measured — 2026-08-12

The delta sweep that set `intra_paper_delta`. Re-measure after any change to
`EMBEDDING_MODEL`, either `EMBEDDING_*_PREFIX`, or the corpus.

- corpus: 4527 chunks / 100 papers (project `fa2ab869…52922`)
- model: `text-embedding-3-small`; ceiling `0.85`; budget `max_context_chunks = 60`
- golden set: **30 positives** (20 of them in papers larger than the 60-chunk
  budget) and **12 negatives** — clears the harness's confidence gate
  (`_MIN_POSITIVES_FOR_CONFIDENCE = 20`, `_MIN_NEGATIVES_FOR_CONFIDENCE = 10`),
  no `ERRORS` block

| delta | survival@cut | mean kept chunks | mean kept tokens | worst off_topic kept |
|-------|--------------|------------------|------------------|----------------------|
| 0.15  | 0.93 (28/30) | 17.7             | ~7,770           | 28                   |
| **0.20** | **1.00 (30/30)** | **27.5**  | **~12,072**      | 52                   |
| 0.25  | 1.00 (30/30) | 36.2             | ~15,906          | 60                   |
| 0.30  | 1.00 (30/30) | 42.4             | ~18,614          | 60                   |
| 0.35  | 1.00 (30/30) | 46.8             | ~20,545          | 60                   |

**Chosen: 0.20** — the smallest delta losing no answer chunk, and delta is the
cost lever. The two cases that fail at 0.15 are `iot-lowpower-protocols`
(answer at intra-rank 18 of 97, 0.1651 from its paper's nearest chunk) and
`ground-control-station` (rank 11 of 18, 0.1640). 0.1651 is therefore the
exact floor, so 0.20 carries **0.035** of margin — thinner than the 0.05 a
fresh tuning would aim for. Every other positive needs ≤0.1212, and 16 of the
30 answer at intra-rank 1 (required delta 0.0).

**That margin is, if anything, optimistic.** Several of the added questions
are near-verbatim paraphrases of the substring they expect —
`iot-lowpower-protocols`, `episode-duration-obstacle-figure`,
`harvested-power-duration-figure`, `frontier-mesh-clustering` and
`jamming-policy-algorithm` — which biases their `intra_rank` low, because the
question and the answering chunk share surface wording a real user's phrasing
would not. `iot-lowpower-protocols` is one of them *and* is the binding
witness at 0.1651, so a more naturally-worded version of that question would
plausibly need a larger delta, not a smaller one. Treat 0.035 as an upper
bound on the true margin, and prefer questions phrased away from their
substring when adding cases.

**Delta is not the active constraint for every case.** At 0.20,
`marl-security-attacks`, `deadly-triad`, `lazy-agents-reward` and
`hnpfl-fair-comparison` keep exactly 60 chunks — the `max_context_chunks`
budget bound before the delta did. No delta value moves those rows; only the
budget does.

**Open finding — the ceiling, not the delta, is the loose one.** The
`ceiling check` line fires at every delta in the sweep. The 0.85 ceiling was
tuned against three trivially off-topic questions sitting at 0.75–0.86; the
near-domain negatives added here sit at **0.547–0.652**, comfortably inside
it, so at the chosen delta a mis-targeted near-domain question keeps 9–52
chunks of the wrong paper instead of the ≤2 the check wants. That range is the
`kept` column of the nine near-domain `off_topic` rows in the delta-0.20
per-case table — 9 (`offtopic-airworthiness`) to 52
(`offtopic-spray-nozzle`); the `ceiling check` summary line prints only the
worst of them. Delta bounds the damage (worst 28 chunks at 0.15, 52 at 0.20,
60 at 0.25) but cannot fix it — the ceiling is a separate constant and a
separate piece of work.

**Positives and near-domain negatives overlap on this corpus.** The worst
positive's top-60 distance is 0.5871 (`hnpfl-fair-comparison`), and six
negatives sit below it (0.547–0.585). Rewriting them further from the corpus
was attempted and measured: thirteen alternative phrasings all landed in
0.469–0.585, i.e. no genuinely near-domain question on a 100-paper
single-topic UAV library can be pushed above the hardest positive. The
nearest chunk of each was read and none answers its question, so these are
real negatives; the overlap is a property of the corpus, not a defect in the
cases. **Keeping them is a ratified deviation from the plan's Step 2 rule**
("drop or rewrite a negative whose best distance sits below the worst
positive"), signed off by the plan author on the grounds that the rule assumed
a separable corpus and 100 papers on one topic is not one — it is not an
oversight, and it should not be "fixed" back.

**What the closed-form section reports, and why.** That run prints
`NO SEPARATION POSSIBLE AT THIS k`, and the overlap above is *not* the cause.
`run_eval.py` prints that message only when `diagnosis.blocked_case_ids` is
non-empty, and `metrics.diagnose_separation` returns early on blocked cases
before `lo`/`hi` are ever computed — the negatives never enter that decision.
The cause is the four positives with no satisfying chunk inside a global
top-60 (listed below). Separately, and independently of those four, the
overlap *would* leave no interval either: `lo = 0.5871` (worst positive) is
already `>= hi = 0.5474` (closest negative), which is the condition for the
**different** message, `NO THRESHOLD SEPARATES CONTENT FROM NOISE`. Two
distinct findings — do not read the printed blocked-case message as a signal
about negatives quality.

Global report from the same run, for the record: `recall@60 = 0.87`,
`MRR = 0.527`, noise floor `0.5474`. Four positives
(`ground-control-station`, `drl-subagent-decomposition`,
`demand-algorithm-baselines`, `epec-stackelberg`) have no satisfying chunk
inside a *global* top-60 at all, yet all four survive the single-paper cut —
which is precisely the gap `--targeted` exists to measure.

**How to re-run the sweep.** `intra_paper_delta` is a pydantic setting, so
each sweep point is an env override on the exec — no code edit, no restart,
and nothing to remember to put back:

    for d in 0.15 0.20 0.25 0.30 0.35; do
      echo "delta=$d"
      docker compose exec -T -e INTRA_PAPER_DELTA=$d backend \
        python -m evals.retrieval.run_eval \
        --project-id <uuid> --targeted | tail -4
    done

Check the printed `targeted mode (ceiling=... delta=... budget=...)` header
changes across points. If it does not, the override is not reaching the
process and every row is the same delta measured five times. One run costs one
embedding API call per case, so a five-point sweep over ~40 cases is ~200
calls — cheap, but not free; do not re-run it to confirm a documentation edit.

## Adding a case

Edit `golden_set.json`. Ground truth is **substrings, not chunk ids** — ids are
regenerated on every re-index, so id-based truth would rot immediately.

    {"id": "unique-slug", "kind": "content",
     "question": "...",
     "paper_title_contains": "distinctive part of the title",
     "expect_substrings": ["phrase that must appear in a correct chunk"]}

`kind` is `content`, `metadata`, `figure`, or `off_topic`. A case passes when a
retrieved chunk is from the expected paper **and** contains **every** listed
substring.

**Verify your substring exists in the target paper before adding it** — not
just anywhere in the corpus. A count across the whole corpus can be nonzero
while your case is still unwinnable, if every hit is in a *different* paper
than the one `paper_title_contains` names — `chunk_satisfies` requires both
conditions on the *same* chunk. Join through `papers` to check the real
condition:

    docker compose exec -T db psql -U researcherx -d researcherx -c \
      "select count(*) from paper_chunk_embeddings c join papers p on p.id = c.paper_id \
       where p.title ilike '%distinctive part of the title%' and c.text ilike '%your phrase%';"

**Prefer a tight substring over a loose one.** A phrase that appears in a
large fraction of the paper's own chunks (e.g. a word like "segmentation" in
a paper that's *about* segmentation) can pass at k=5 by pure luck, no matter
which 5 chunks retrieval happens to return — hypergeometric odds, not
retrieval quality. Roughly: for a paper with `N` chunks and `K` of them
containing your substring, the probability a *random* 5-chunk draw already
contains a hit is `1 - C(N-K, 5) / C(N, 5)`. Aim for a phrase specific to the
actual answering passage (a named dataset, a distinctive multi-word phrase),
not any word that happens to recur throughout the paper.

`off_topic` cases carry no expectations — they assert that nothing relevant
exists, and they are what make a separating threshold measurable at all. The
runner refuses to compute one without usable negatives.

## Reading the output

- **the per-case table** (`case  kind  rank  best_dist`):
  - **rank** is the chunk's 1-based position in `simulate_retrieval`'s
    distance-sorted global top-`k`, so a non-`-` rank is always `<= --k` —
    there's no larger pool to rank within. (Before 2026-08-10, when this
    simulated top-k *per paper*, a legitimate rank could exceed `--k`; a
    global top-k has no such headroom, by construction.)
  - **`-`** means three different things depending on the column and row: for
    a positive/metadata/figure case, a `-` rank means no satisfying chunk
    survived this case's own top-`k` (it may still exist elsewhere in the
    corpus — see `best_dist` on that same row); for an `off_topic` case,
    `rank` is always `-` because negatives have no "satisfying chunk" to
    rank; and `best_dist` is `-` only when a case retrieved zero chunks at
    all (corpus empty for that model). Don't read a `-` as "zero" or as
    "identical to the row above" — check which column and which kind.
  - **best_dist** is the closest distance among *all* chunks in the whole
    corpus that satisfy the case — **not top-k-aware**. It ignores whether
    nearer chunks crowd the satisfying chunk out of the top-`k` that
    production (and `rank`) actually use, so it can report a small,
    encouraging number for a case whose rank is `-`. Do not read a small
    `best_dist` next to a `-` rank as "so lower the threshold" — that's
    precisely the inference the closed-form section below refuses to make
    (see "NO SEPARATION POSSIBLE AT THIS k"). The `--json` dump's per-case
    `topk_distance` field is the top-k-aware equivalent, and `blocked` says
    outright whether that number exists.
- **recall@k** / **MRR** — the retrieval *ceiling*: no distance cutoff is
  applied, only the global top-k. The LLM can ignore an irrelevant chunk but
  cannot use one that never arrived, so this is the metric that matters most
  — but pair it with the closed-form section below for what a real
  `similarity_threshold` cutoff actually leaves production with, and see the
  console's own printed caveat (no cutoff; `k` is a global budget matching
  production's `max_context_chunks`).
- **noise floor** — the closest distance any off-topic question achieved. A
  threshold is only meaningful below this and above the content distances.
- **threshold sweep grid** — a fixed 0.05-step table, kept as a coarse visual
  aid and cross-check. It is **not** the authoritative answer: a real
  separating interval can be narrower than the grid step and fall entirely
  between two sampled points, in which case the grid reports "no row
  achieves separation" even though an exact one exists (this happened on
  this harness's own first live corpus — see "Findings" below). Read
  "no row in the swept grid achieves separation" as exactly that claim about
  the grid, not as "no absolute cutoff can work for this model" — for that,
  read the closed-form section. In `--json`, this is the `sweep` key, whose
  `note` field repeats this in-band; `sweep.rows` is the raw grid.
- **closed-form separating interval** — the exact, authoritative computation
  the grid was only approximating: the precise `(lo, hi]` interval within
  which every threshold achieves full content recall and zero off-topic
  acceptance, computed directly rather than sampled. In `--json`, this is the
  `separation` key (`lo`, `hi`, `lo_case_id`, `hi_case_id`,
  `blocked_case_ids`, `k`); it is `null` when there was nothing to diagnose
  (no usable negatives, or no scored positives). Three outcomes:
  - **NO SEPARATION POSSIBLE AT THIS k** — a positive case's satisfying
    chunk doesn't survive its own top-k at all, for any threshold. The fix
    is `--k` or retrieval ranking, not the threshold.
  - **NO THRESHOLD SEPARATES CONTENT FROM NOISE** — a genuine, exactly
    computed non-separation (the worst content distance is >= the closest
    off-topic distance). The next lever is reranking or hybrid retrieval,
    not a better constant.
  - **SEPARATION FOUND** — an interval exists. It prints as **PROVISIONAL,
    DO NOT SHIP** unless there are at least 20 positives *and* at least 10
    negatives *and* the margin exceeds the spread of the negatives that
    define it. A narrow interval on a small golden set is usually set by one
    case on each side (the report shows a leave-one-out check: what the
    interval becomes if the deciding positive case is dropped) and can
    evaporate the moment the set grows — the printed order-statistics odds
    quantify exactly how fragile it is. If `--k` isn't production's own
    operating point (`max_context_chunks`), the report also names that
    divergence explicitly (see "What it measures vs. what production does").
- **ROBUST FINDING** — the current `similarity_threshold`'s off-topic
  acceptance rate on this set, printed unconditionally. Unlike the
  interval above, this survives resampling: it doesn't depend on finding an
  exact boundary, only on whether today's threshold already admits noise.

Results are model-specific. Re-baseline after any change to
`EMBEDDING_MODEL` or either `EMBEDDING_*_PREFIX`.

## Findings

Dated measurements from the live dev corpus at the time this branch's final
review was fixed: **2026-08-05**, corpus **122 chunks / 4 papers** (the dev
corpus has since grown to 100 papers — see the warning at the top of this
file), model **nomic-embed-text**. These numbers also predate the
2026-08-10 move to a global top-k (see "What it measures vs. what production
does") and are not comparable to a current run for either reason. Re-baseline
(re-run and update this section) after any change to `EMBEDDING_MODEL`,
either `EMBEDDING_*_PREFIX`, or the corpus.

- **Per-paper top-k was chunk-count-blind — the biggest finding this harness
  produced, and the direct motivation for removing the per-paper ceiling on
  2026-08-10.** `segmentation-datasets`'s answering chunk sat at distance
  0.2048 — among the closest in the entire corpus — but at within-paper rank
  18 of 67, so it never surfaced at `--k` 5, 8, or 12; it needed `--k 20`.
  Seventeen chunks from its own 67-chunk paper outranked it. A 67-chunk paper
  and a 12-chunk paper received the same per-paper budget of `k`, so large
  papers were starved in proportion to their size. No threshold could fix
  this: threshold-filtering and top-k-selection are both nearest-prefix
  operations on the same distance-sorted list (see
  `metrics.topk_satisfying_distance`'s docstring for the proof), so a chunk
  already excluded from the unfiltered top-k could never be recovered by any
  threshold. Production now applies no per-paper ceiling at all, which
  removes this failure mode outright rather than just tuning around it.
- **Robust finding**: `similarity_threshold = 0.75` (the shipped default)
  accepted **100%** of off-topic questions in this golden set — the opposite
  of a working guard rail. Survives resampling in the sense that it doesn't
  depend on finding an exact separating boundary, only on whether the
  threshold already admits noise, which it did for every negative case in
  the set at the time.
- **Measured baseline** (`--k 5` per paper — this harness's old semantics,
  this corpus, this model): `recall@5 = 0.88`, `MRR = 0.589`, noise floor
  `0.4749`. Not comparable to a `recall@5` from this harness post-2026-08-10
  (see the warning at the top of this file); read it only as what the
  per-paper simulation reported at the time.

This section records a historical finding; it does not change retrieval
behavior. The per-paper ceiling it describes has since been removed from
production; a fresh baseline against the current (100-paper) corpus, at
`--k = max_context_chunks`, is separate work.
