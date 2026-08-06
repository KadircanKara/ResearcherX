# Retrieval eval harness

Measures retrieval quality against the **live dev database**. Measures only —
it never writes.

    docker compose exec -T backend python -m evals.retrieval.run_eval
    docker compose exec -T backend python -m evals.retrieval.run_eval --k 3 --json /tmp/retrieval.json

The `-m` form is required: `pyproject.toml` packages only `app*`, so `evals`
isn't installed and running it by file path fails with
`ModuleNotFoundError: No module named 'app'`.

Flags:

- `--k` — chunks per paper (default: 5). Uniform across every paper; see
  "What it measures vs. what production does" below for how that differs
  from production's per-paper allocation.
- `--set` — path to an alternate golden-set JSON file (default: `golden_set.json`
  next to this file, same schema as "Adding a case" below).
- `--json` — also write the full per-case results, threshold-sweep grid, and
  closed-form separation to this path. See "Reading the output" for the
  `--json` shape and how it differs from the console report.

## What it measures vs. what production does

The runner's SQL mirrors production's **distance computation** exactly (same
`<=>` cosine operator, same `WHERE model = :model` filter) but not its
**retrieval path**. Production (`chat_service.py`) runs a planning step
whenever a project has 3+ papers — true of the current dev corpus — which
embeds a *reformulated* query (not the user's raw question, `chat_service.py:104`)
and allocates a different `k` per paper (1-6 per the planner's schema,
commonly 2 or 5 in practice; a paper the planner ran but didn't explicitly
allocate falls back to `k=2`, `chat_service.py:239` — not this harness's
default of 5). This harness always embeds the golden-set `question` verbatim
and applies one uniform `--k` to every paper. That's a deliberate
simplification for reproducibility, not an oversight — but it
means a threshold calibrated here is calibrated on raw-question distances,
and production would apply it to reformulated-query distances. Re-validate
after any change to the planner's reformulation behavior, not just after an
embedding model change.

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
  - **rank** is the chunk's 1-based position in the *merged, distance-sorted*
    list `simulate_retrieval` returns — up to `papers × k` chunks, not just
    `k` — so a legitimate rank can exceed `--k` (e.g. rank 12 at `--k 5` with
    4 papers). It is **not** a rank within one paper's own budget.
  - **`-`** means three different things depending on the column and row: for
    a positive/metadata/figure case, a `-` rank means no satisfying chunk
    survived this case's own per-paper top-`k` (it may still exist elsewhere
    in the corpus — see `best_dist` on that same row); for an `off_topic`
    case, `rank` is always `-` because negatives have no "satisfying chunk"
    to rank; and `best_dist` is `-` only when a case retrieved zero chunks at
    all (corpus empty for that model). Don't read a `-` as "zero" or as
    "identical to the row above" — check which column and which kind.
  - **best_dist** is the closest distance among *all* chunks in the whole
    corpus that satisfy the case — **not top-k-aware**. It ignores whether
    nearer same-paper chunks crowd the satisfying chunk out of the per-paper
    top-`k` that production (and `rank`) actually use, so it can report a
    small, encouraging number for a case whose rank is `-`. Do not read a
    small `best_dist` next to a `-` rank as "so lower the threshold" — that's
    precisely the inference the closed-form section below refuses to make
    (see "NO SEPARATION POSSIBLE AT THIS k"). The `--json` dump's per-case
    `topk_distance` field is the top-k-aware equivalent, and `blocked` says
    outright whether that number exists.
- **recall@k** / **MRR** — the retrieval *ceiling*: no distance cutoff is
  applied, only the per-paper top-k. The LLM can ignore an irrelevant chunk
  but cannot use one that never arrived, so this is the metric that matters
  most — but pair it with the closed-form section below for what a real
  `similarity_threshold` cutoff actually leaves production with, and see the
  console's own printed caveats (no cutoff; `--k` is uniform here, production
  allocates per paper; the query is the raw question, not the planner's
  reformulation).
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
    chunk doesn't survive its own paper's top-k at all, for any threshold.
    The fix is `--k` or retrieval ranking, not the threshold.
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
    operating range, the report also names that divergence explicitly (see
    "What it measures vs. what production does").
- **ROBUST FINDING** — the current `similarity_threshold`'s off-topic
  acceptance rate on this set, printed unconditionally. Unlike the
  interval above, this survives resampling: it doesn't depend on finding an
  exact boundary, only on whether today's threshold already admits noise.

Results are model-specific. Re-baseline after any change to
`EMBEDDING_MODEL` or either `EMBEDDING_*_PREFIX`.

## Findings

Dated measurements from the live dev corpus at the time this branch's final
review was fixed: **2026-08-05**, corpus **122 chunks / 4 papers**, model
**nomic-embed-text**. Re-baseline (re-run and update this section) after any
change to `EMBEDDING_MODEL` or either `EMBEDDING_*_PREFIX` — these numbers
are specific to that embedding space and will not hold after either changes.

- **Per-paper top-k is chunk-count-blind — the biggest finding this harness
  has produced.** `segmentation-datasets`'s answering chunk sits at distance
  0.2048 — among the closest in the entire corpus — but at within-paper rank
  18 of 67, so it never surfaces at `--k` 5, 8, or 12; it needs `--k 20`.
  Seventeen chunks from its own 67-chunk paper outrank it. A 67-chunk paper
  and a 12-chunk paper receive the same per-paper budget of `k`, so large
  papers are starved in proportion to their size. **No threshold can fix
  this**: threshold-filtering and top-k-selection are both nearest-prefix
  operations on the same distance-sorted list (see
  `metrics.topk_satisfying_distance`'s docstring for the proof), so a chunk
  already excluded from the unfiltered top-k can never be recovered by any
  threshold. The fix, if one is wanted, is `--k` or retrieval ranking — not
  `similarity_threshold`.
- **Robust finding**: `similarity_threshold = 0.75` (the shipped default)
  accepts **100%** of off-topic questions in the current golden set — the
  opposite of a working guard rail. Survives resampling in the sense that it
  doesn't depend on finding an exact separating boundary, only on whether
  today's threshold already admits noise, which it does for every negative
  case currently in the set.
- **Measured baseline** (`--k 5`, this corpus, this model): `recall@5 =
  0.88`, `MRR = 0.589`, noise floor `0.4749`. Read `recall@5` as the
  retrieval ceiling (no distance cutoff applied), not as what production
  actually returns — see "Reading the output" above.

This section records findings; it does not change retrieval behavior. Any
fix (raising `--k`'s production analogue, retuning `similarity_threshold`,
reranking) is separate work.
