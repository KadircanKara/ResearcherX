# Retrieval eval harness

Measures retrieval quality against the **live dev database**. Measures only —
it never writes.

    docker compose exec -T backend python -m retrieval_eval.run_eval
    docker compose exec -T backend python -m retrieval_eval.run_eval --k 3 --json /tmp/retrieval_eval.json

The `-m` form is required: `pyproject.toml` packages only `app*`, so `retrieval_eval`
isn't installed and running it by file path fails with
`ModuleNotFoundError: No module named 'app'`.

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

**Verify your substring exists before adding it.** A case that can never pass
reads as a retrieval failure:

    docker compose exec -T db psql -U researcherx -d researcherx -c \
      "select count(*) from paper_chunk_embeddings where text ilike '%your phrase%';"

`off_topic` cases carry no expectations — they assert that nothing relevant
exists, and they are what stop the sweep from recommending a cutoff that simply
admits everything. The runner refuses to recommend a threshold without them.

## Reading the output

- **recall@k** — the metric that matters most: the LLM can ignore an irrelevant
  chunk but cannot use one that never arrived.
- **noise floor** — the closest distance any off-topic question achieved. A
  threshold is only meaningful below this and above the content distances.
- **sweep** — content recall vs off-topic acceptance at each cutoff. If no row
  achieves full recall with zero acceptance, an absolute distance cutoff cannot
  work for this model, and the next lever is reranking or hybrid retrieval.

Results are model-specific. Re-baseline after any change to
`EMBEDDING_MODEL` or either `EMBEDDING_*_PREFIX`.
