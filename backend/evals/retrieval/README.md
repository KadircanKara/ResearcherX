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

`_production_cut` then applies the cut to that scoped, distance-sorted list
in production's exact order — SQL filters on the ceiling, LIMITs to
`max_context_chunks`, *then* the delta cut runs in Python — because that
order matters: budget-then-delta and delta-then-budget can keep different
chunks. The cut itself is `keep_within_paper`, **imported** from
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

`survival@cut` at the bottom is `survived == yes` count over scored
(non-off_topic) cases — the single number to watch when re-tuning
`intra_paper_delta`. The `ceiling check` line beneath it is the off_topic
side of the same coin: the worst (largest) `kept` count across off_topic
rows, flagged if it exceeds 2 — a large kept-count on a mis-targeted
off_topic case means the ceiling itself is too loose, independent of delta.

In `--json`, this is the `targeted` key (a list of the same per-case rows,
or `null` when `--targeted` wasn't passed).

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
