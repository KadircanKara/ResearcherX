# Retrieval eval harness

Measures retrieval quality against the **live dev database**. Measures only —
it never writes.

    docker compose exec -T backend python -m retrieval_eval.run_eval
    docker compose exec -T backend python -m retrieval_eval.run_eval --k 3 --json /tmp/retrieval_eval.json

The `-m` form is required: `pyproject.toml` packages only `app*`, so `retrieval_eval`
isn't installed and running it by file path fails with
`ModuleNotFoundError: No module named 'app'`.

## What it measures vs. what production does

The runner's SQL mirrors production's **distance computation** exactly (same
`<=>` cosine operator, same `WHERE model = :model` filter) but not its
**retrieval path**. Production (`chat_service.py`) runs a planning step
whenever a project has 3+ papers — true of the current dev corpus — which
embeds a *reformulated* query (not the user's raw question) and allocates a
different `k` per paper. This harness always embeds the golden-set
`question` verbatim and applies one uniform `--k` to every paper. That's a
deliberate simplification for reproducibility, not an oversight — but it
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

- **recall@k** / **MRR** — the retrieval *ceiling*: no distance cutoff is
  applied, only the per-paper top-k. The LLM can ignore an irrelevant chunk
  but cannot use one that never arrived, so this is the metric that matters
  most — but pair it with the closed-form section below for what a real
  `similarity_threshold` cutoff actually leaves production with.
- **noise floor** — the closest distance any off-topic question achieved. A
  threshold is only meaningful below this and above the content distances.
- **threshold sweep grid** — a fixed 0.05-step table, kept as a coarse visual
  aid and cross-check. It is **not** the authoritative answer: a real
  separating interval can be narrower than the grid step and fall entirely
  between two sampled points, in which case the grid reports "no row
  achieves separation" even though an exact one exists (this happened on
  this harness's own first live corpus — see the task report). Read
  "no row in the swept grid achieves separation" as exactly that claim about
  the grid, not as "no absolute cutoff can work for this model" — for that,
  read the closed-form section.
- **closed-form separating interval** — the exact, authoritative computation
  the grid was only approximating: the precise `(lo, hi]` interval within
  which every threshold achieves full content recall and zero off-topic
  acceptance, computed directly rather than sampled. Three outcomes:
  - **NO SEPARATION POSSIBLE AT THIS k** — a positive case's satisfying
    chunk doesn't survive its own paper's top-k at all, for any threshold.
    The fix is `--k` or retrieval ranking, not the threshold.
  - **NO THRESHOLD SEPARATES CONTENT FROM NOISE** — a genuine, exactly
    computed non-separation (the worst content distance is >= the closest
    off-topic distance). The next lever is reranking or hybrid retrieval,
    not a better constant.
  - **SEPARATION FOUND** — an interval exists. It prints as **PROVISIONAL,
    DO NOT SHIP** unless there are at least 10 negatives *and* the margin
    exceeds the spread of the negatives that define it. A narrow interval on
    a small golden set is usually set by one case on each side (the report
    shows a leave-one-out check: what the interval becomes if the deciding
    positive case is dropped) and can evaporate the moment the set grows —
    the printed order-statistics odds quantify exactly how fragile it is.
- **ROBUST FINDING** — the current `similarity_threshold`'s off-topic
  acceptance rate on this set, printed unconditionally. Unlike the
  interval above, this survives resampling: it doesn't depend on finding an
  exact boundary, only on whether today's threshold already admits noise.

Results are model-specific. Re-baseline after any change to
`EMBEDDING_MODEL` or either `EMBEDDING_*_PREFIX`.
