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
- `--hybrid` — also run the hybrid arm (dense pgvector + sparse Postgres FTS,
  fused by weighted RRF) beside the dense-only baseline, and report `rescued`:
  positives whose answering chunk reached the budget **only** via the sparse
  arm. Costs one extra query per positive case, plus one per targeted positive
  when combined with `--targeted`. See "Measured — 2026-08-15" below.

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

### Measured — 2026-08-15 (hybrid: dense + sparse RRF)

The sweep that measures the hybrid arm (`--hybrid`). Re-measure after any
change to `EMBEDDING_MODEL`, either `EMBEDDING_*_PREFIX`, the corpus, or the
sparse arm's `tsquery` construction.

- corpus: **4527 chunks / 100 papers** (project `fa2ab869…52922`) — the same
  project the 2026-08-12 delta tuning used, so the two blocks are comparable
- model: `text-embedding-3-small`
- constants in play: `similarity_threshold 0.75`, `intra_paper_ceiling 0.85`,
  `max_context_chunks 60`, `hybrid_dense_pool 200`, `hybrid_sparse_pool 100`;
  swept: `hybrid_sparse_weight` / `hybrid_dense_weight`, `hybrid_rrf_k`,
  `intra_paper_rank_window`
- golden set: unchanged from 2026-08-12 — **30 positives**, **12 negatives**,
  no `ERRORS` block
- dense-only baseline on this set: `recall@60 = 0.87` (26/30), `MRR = 0.527`,
  noise floor `0.5474`

#### First: the dense arm is saturated, the sparse arm is nearly empty

Two corpus facts decide how every number below reads. Measured per golden-set
question against production's own gate (`distance < similarity_threshold`,
`LIMIT hybrid_dense_pool`) and production's own sparse predicate
(`c.tsv @@ websearch_to_tsquery('english', question)`):

| arm | per-question size, 30 positives |
|-----|---------------------------------|
| dense, pooled | **200 on all 30** (pool limit); ungated it is 1824–4146 chunks |
| sparse | **0 on 22 of 30**; 1–5 on the other 8; median **0** |

1. **The dense arm never returns fewer than the pool cap**, so the RRF
   arithmetic the plan worried about binds on every single question. A
   sparse-only chunk at sparse rank 1 scores `w_s/(k+1)`; the dense chunk at
   the budget edge scores `w_d/(k+60)`. Sparse-only admission into a 60-chunk
   budget therefore requires `w_s·(k+60) > w_d·(k+1)`. At the shipped
   `0.7 / 0.3` weights that inequality is `0.3(k+60) > 0.7(k+1)` →
   `17.3 > 0.4k` → **k < 43.25**, i.e. the largest usable integer k is 43.
   It is false for `k = 60` (36 vs 42.7) and true for `k ≤ 43`. The sweep
   below (rescued=2 at k=30, rescued=0 at k=60) is consistent with that
   crossover but does not by itself pin it — the arithmetic does.
   **`rescued = 0` at the default constants was a parameter artifact, not
   evidence against lexical retrieval** — see the sweep.
2. **`websearch_to_tsquery` ANDs every term**, so a full-sentence question
   requires a chunk containing all of its content words. That is why the
   sparse arm is empty on 22 of 30 questions. The answering chunk is present
   anywhere in the sparse arm on **6 of 30** cases — that number, not the
   weights and not `rrf_k`, is the hard ceiling on how much this arm can ever
   contribute on this corpus. Replacing the AND query with an OR-of-lexemes
   query (diagnostic only, not shipped) raises those two figures to **30 of
   30** matched and **26 of 30** with the answering chunk in the sparse
   top-100. The lexical signal exists; the shipped query construction is what
   discards it.

#### Sweep 1 — `hybrid_sparse_weight` × `hybrid_rrf_k` (global scope, `--k 60`)

`survival@cut` is 1.00 (30/30) and worst off_topic `kept` is 52 at **every**
point in this table — neither moves, for reasons given under "What this does
not establish". Only recall, MRR and `rescued` vary.

| w_dense / w_sparse | rrf_k | hybrid recall@60 | hybrid MRR | rescued (of 30 positives) | rescued (of 4 eligible) |
|--------------------|-------|------------------|------------|---------------------------|-------------------------|
| 1.0 / 0.0 (control) | 60 | 0.87 | 0.527 | 0 | 0 |
| 0.9 / 0.1 | 60 | 0.87 | 0.523 | 0 | 0 |
| 0.8 / 0.2 | 60 | 0.87 | 0.543 | 0 | 0 |
| **0.7 / 0.3 (shipped)** | **60** | **0.87** | **0.561** | **0** | **0** |
| 0.6 / 0.4 | 60 | 0.93 | 0.594 | 2 | 2 |
| 0.5 / 0.5 | 60 | 0.93 | **0.608** | 2 | 2 |
| 0.9 / 0.1 | 30 | 0.87 | 0.522 | 0 | 0 |
| 0.8 / 0.2 | 30 | 0.87 | 0.525 | 0 | 0 |
| **0.7 / 0.3** | **30** | **0.93** | **0.562** | **2** | **2** |
| 0.6 / 0.4 | 30 | 0.93 | 0.570 | 2 | 2 |
| 0.5 / 0.5 | 30 | 0.93 | 0.608 | 2 | 2 |
| 0.7 / 0.3 | 20 | 0.93 | 0.538 | 2 | 2 |
| 0.8 / 0.2 | 10 | 0.93 | 0.526 | 2 | 2 |
| 0.9 / 0.1 | 10 | 0.87 | 0.528 | 0 | 0 |
| 0.7 / 0.3 | 10 | 0.93 | 0.533 | 2 | 2 |
| 0.9 / 0.1 | 5 | 0.93 | 0.530 | 2 | 2 |
| 0.8 / 0.2 | 5 | 0.93 | 0.534 | 2 | 2 |
| 0.7 / 0.3 | 5 | 0.93 | 0.536 | 2 | 2 |

**`rescued` never exceeds 2 anywhere in the swept space**, and every point
that reaches 2 reaches it on the same two cases. The pattern matches the
admission inequality above exactly: `rescued` is 2 wherever
`w_s·(k+60) > w_d·(k+1)` holds and 0 wherever it does not. `hybrid_rrf_k` and
`w_sparse` are the same lever here, not two.

#### The binding witnesses

The eligible set is **exactly 4** on this corpus, not "at least 4": the
harness's `rescue_eligible_count` computes the dense arm from `_SQL`, which
applies no distance gate and no pool limit, so in general it can undercount.
It does not here — production's gated dense arm admits ≥200 chunks on every
positive, so its nearest 60 and the ungated nearest 60 are the same set by
construction. Verified per case, not assumed.

| eligible case | dense rank in top-60 | hybrid (0.7/0.3, k=30): fused rank, d_rank, s_rank | outcome |
|---------------|----------------------|----------------------------------------------------|---------|
| `ground-control-station` | absent | 43, `d_rank=None`, `s_rank=1` | **rescued** |
| `demand-algorithm-baselines` | absent | 43, `d_rank=None`, `s_rank=1` | **rescued** |
| `drl-subagent-decomposition` | absent | not in top-60 | not rescued |
| `epec-stackelberg` | absent | not in top-60 | not rescued |

Both rescues are genuine sparse-only admissions (`d_rank is None`) — the
chunk did not pass the dense gate at all and arrived purely on the lexical
arm. The two failures are not a tuning problem: `websearch_to_tsquery`
returns **zero** sparse matches for both of those questions, so no weight and
no `rrf_k` can rescue them. 2 of 4 is the maximum this arm can reach with its
current query construction.

#### Sweep 2 — `intra_paper_rank_window` (targeted / single-paper scope)

At `0.7 / 0.3`, `rrf_k = 30`, budget 60. Note `rrf_k` does **not** bind in
this scope: a single paper contributes 16–127 chunks, and the rescued-in-scope
chunks here are in *both* arms, so they gain from fusion rather than needing
sparse-only admission.

| rank window | survival@cut | mean kept chunks | mean kept tokens (~439/chunk) |
|-------------|--------------|------------------|-------------------------------|
| 1  | 0.63 (19/30) | 1.0  | ~439 |
| 2  | 0.80 (24/30) | 2.0  | ~878 |
| 3  | 0.90 (27/30) | 3.0  | ~1,317 |
| **5**  | **1.00 (30/30)** | **5.0** | **~2,195** |
| 8  | 1.00 (30/30) | 8.0  | ~3,512 |
| 10 | 1.00 (30/30) | 10.0 | ~4,390 |
| 15 | 1.00 (30/30) | 15.0 | ~6,585 |
| 20 | 1.00 (30/30) | 19.7 | ~8,648 |
| 25 | 1.00 (30/30) | 24.1 | ~10,580 |
| **30 (shipped)** | **1.00 (30/30)** | **28.4** | **~12,468** |
| 40 | 1.00 (30/30) | 36.6 | ~16,067 |
| 60 | 1.00 (30/30) | 51.8 | ~22,740 |

**Do not read this table as "ship window = 5."** The deepest fused intra-rank
under hybrid is 5, but that number is produced *by* the sparse arm, and the
sparse arm is silent on 22 of 30 questions. On those 22 the fused order is
literally the dense order — verified by running the control arm
(`w_sparse = 0.0`), whose fused intra-ranks match the dense delta path's
intra-ranks on all 30 cases — and the deepest dense intra-rank is **18**
(`iot-lowpower-protocols`). A window of 5 or 10 would cut that case's answer
on any question phrasing the AND-query misses. **18 is the safety floor;
`intra_paper_rank_window = 30` carries 12 ranks over it.** Windows 20 and 25
are defensible; anything below 20 is not, on this evidence.

#### Is the rank-53 class of failure fixed?

**Improved and, in single-paper scope, materially so — but not fixed.**
Where the sparse arm fires, fusion moves a deep answer to the top of the
paper. At `0.7 / 0.3`, single-paper scope, against the `w_sparse = 0.0`
control:

| case | dense intra-rank | fused intra-rank |
|------|------------------|------------------|
| `iot-lowpower-protocols` | 18 | **1** |
| `ground-control-station` | 11 | **1** |
| `u2o-collision-trigger` | 10 | **1** |
| `demand-algorithm-baselines` | 5 | **1** |
| `frontier-mesh-clustering` | 1 | 2 (slight regression) |

`iot-lowpower-protocols` was the exact binding witness of the 2026-08-12
delta tuning (intra-rank 18 of 97, forcing `intra_paper_delta = 0.20` with
only 0.035 of margin). Under hybrid it answers at rank 1. That is the class
of failure the branch exists to address, and on this corpus it is addressed
— **on the 8 of 30 questions where the sparse arm returns anything at all.**
On the other 22 the hybrid path is bit-for-bit the dense path, and any
rank-53-style failure among them is untouched. Globally, recall moves
0.87 → 0.93 and 2 of the 4 dense misses are recovered; 2 remain missed at
every point in the parameter space.

#### What this measurement does NOT establish

- **It does not establish that hybrid retrieval helps on this corpus in
  general.** 2 rescues on 30 positives, from 8 questions where the lexical
  arm was non-empty, is a small-sample result on a single 100-paper
  single-topic library. Both rescues rest on a single sparse rank-1 chunk
  each.
- **It says nothing about off_topic containment under hybrid.** The
  `hybrid targeted mode` report covers non-negative cases only, by
  construction (`_hybrid_targeted_row` is never built for a negative). The
  worst off_topic `kept = 52` printed in every run above is the **dense
  delta path**, unchanged from 2026-08-12 and unaffected by any hybrid
  constant. Nothing here measures what `intra_paper_rank_window` lets
  through on a mis-targeted paper.
- **`survival@cut = 1.00` is not evidence of hybrid quality.** It is 1.00 at
  every swept point including the `w_sparse = 0.0` control, so it separates
  nothing. It is a regression guard, not a signal.
- **The near-domain overlap of 2026-08-12 is untouched.** The closed-form
  section still prints `NO SEPARATION POSSIBLE AT THIS k` for the same four
  blocked positives; hybrid moves two of them into the budget but the
  harness's separation diagnosis runs on the dense arm only, so that message
  is unchanged by design.
- **Query reformulation is still not simulated** (see "What it measures vs.
  what production does"), and the sparse arm is *more* sensitive to phrasing
  than the dense arm is — an AND-query over a reformulated question can
  match a different number of chunks than over the raw one. The 8-of-30
  sparse hit rate is specific to these verbatim questions.

#### Recommendation (not applied — the constants are the owner's call)

- **`hybrid_rrf_k`: 60 → 30.** This is the only change the measurement
  actually demands. At the owner's stated `0.7 / 0.3` weights, `k = 60`
  blocks sparse-only admission arithmetically, which is what produced the
  `rescued = 0` reading; `k = 30` unblocks it and buys recall 0.87 → 0.93 and
  MRR 0.527 → 0.562 at zero cost. The admission inequality
  (`w_sparse·(k + max_context_chunks) > w_dense·(k + 1)`) flips at
  `k < 43.25`, i.e. the largest usable integer k is 43; 30 keeps 13 ranks of
  margin under that ceiling, not "around k≈35". 20 or 10 also work and are
  not better.
- **Weights: keep `0.7 / 0.3`.** For the record, `0.5 / 0.5` at any `k`
  reaches the same recall and the best MRR in the sweep (0.608 vs 0.562), and
  is the point to consider if MRR is worth more than staying with the stated
  weights. It rescues the same 2 cases — no additional recovery.
- **`intra_paper_rank_window`: keep 30.** Justified against the dense-only
  worst intra-rank of 18, not against hybrid's 5.
- **The real lever is not a constant.** The sparse arm's AND semantics
  discard the signal on 22 of 30 questions. Switching the sparse query from
  `websearch_to_tsquery` to an OR-of-lexemes form takes the arm from 8/30 to
  30/30 non-empty and from 6/30 to 26/30 with the answering chunk present.
  That is a one-line change to the query with a much larger measured
  headroom than any weight, and it must be swept and re-measured on its own
  before shipping — an OR query also admits far more noise, which nothing
  here measures. A BM25 base-image migration should not be evaluated until
  after that.

#### How to re-run

    # weight × rrf_k grid
    for w in 0.0 0.1 0.2 0.3 0.4 0.5; do
      d=$(python3 -c "print(round(1-$w,1))")
      for kk in 60 30 20 10 5; do
        docker compose exec -T \
          -e HYBRID_SPARSE_WEIGHT=$w -e HYBRID_DENSE_WEIGHT=$d -e HYBRID_RRF_K=$kk \
          backend python -m evals.retrieval.run_eval \
            --project-id <uuid> --hybrid --targeted | grep -E "recall|rescued"
      done
    done

    # rank-window sweep
    for win in 5 10 15 20 25 30 40 60; do
      docker compose exec -T -e INTRA_PAPER_RANK_WINDOW=$win backend \
        python -m evals.retrieval.run_eval \
        --project-id <uuid> --hybrid --targeted | grep -A1 "hybrid targeted mode"
    done

Check the printed `hybrid arm (w_dense=… w_sparse=… rrf_k=…)` and
`hybrid targeted mode (rank_window=…)` headers change across points — if they
do not, the override is not reaching the process and every row is one point
measured N times. The weights must sum to exactly 1.0 or `Settings` refuses
to start. One run is ~one embedding call per case plus one per targeted
positive (~72 calls on this set), so a full grid is not free.

## Scoping harness (`shortlist_eval.py`)

`run_eval.py` measures whether the answering CHUNK reached the budget. It
cannot see the failure one layer above: retrieval scoped to the wrong PAPER
returns plenty of chunks, all from that paper, and every chunk-level metric
looks healthy while the answer is grounded in the wrong work.

    docker compose exec -T backend python -m evals.retrieval.shortlist_eval --project-id <uuid>
    docker compose exec -T backend python -m evals.retrieval.shortlist_eval --project-id <uuid> --no-llm

It imports production's own `ChatService._shortlist_papers`, so it cannot
measure a shortlist policy production does not run. `--no-llm` reports
candidate recall only and makes no targeter calls.

Two case sets, and the split is the point:

- **golden** — `golden_set.json` positives, content-worded. Most name no paper
  at all, so the correct targeter outcome for them is `empty` (unscoped), not
  `correct`.
- **scope** — `scope_set.json`, title-referential ("the paper that compares
  evolutionary algorithms against reinforcement learning"). These identify one
  paper, by a property of its TITLE.

Read `WRONG` as the harm metric: a paper missing from the candidate list only
hurts if the targeter then names a paper that does not hold the answer.
Abstaining is safe.

### Measured — 2026-08-18

Live 100-paper project, before and after the lexical title arm was unioned
into the shortlist:

    config                     golden recall   scope recall   golden WRONG   scope WRONG
    dense 10 (before)              28/30           7/8          12/30           1/8
    dense 10 + lexical 10          29/30           8/8          10-11/30         0/8
    all 100 titles offered         30/30           8/8          10/30            0/8

The live failure (conversation `867dd8c5`, `scope_set.json` case
`live-867dd8c5`): the target paper ranked **28th of 100** by nearest-chunk
distance, outside the dense cap, so the targeter named a wrong paper and
retrieval was scoped to it. Under the lexical arm the same paper is offered
and picked correctly.

Two findings worth keeping:

- **RRF fusion of the two arms is wrong here; union is right.** Fusing 50/50
  dropped golden candidate recall 28/30 → 24/30 — a lexical rank exists for
  papers the dense arm was right to bury. A union can only add.
- **Offering all 100 titles buys one golden case over the union** for ~2,900
  extra input tokens per targeted turn, and is O(N) in library size (~23k
  tokens at 1,000 papers). Not shipped.

`golden WRONG` at 10-11/30 is a STANDING DEFECT, not a consequence of this
change: the targeter names a paper on content questions that identify none,
where the safe answer is `empty`. Expanding the candidate list barely moves it
(12 → 10-11, with abstentions rising 8 → 10-12). It needs its own fix; the
existing `chat_service` fallback cannot catch it, since that re-queries
unscoped only when the scoped retrieval returns ZERO chunks.

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
