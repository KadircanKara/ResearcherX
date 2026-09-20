# Evaluation harnesses

Three harnesses, measuring three things that fail independently.

- **`retrieval/`** — does semantic search surface the chunk that answers a
  question? Run: `docker compose exec -T backend python -m evals.retrieval.run_eval`
- **`groundedness/`** — is the ANSWER carried by the excerpts retrieval
  supplied, and do its citation markers point at the papers that carry it?
  Run: `docker compose exec -T backend python -m evals.groundedness.run_eval
  --project-id <uuid>`
- **`metadata/`** — did extraction get each paper's authors, year, and venue
  right, and did it invent any it shouldn't have? Run:
  `docker compose exec -T backend python -m evals.metadata.run_eval`

All three read the **live dev database** and never write to it.

`retrieval/` and `groundedness/` share one golden set and split the pipeline
between them at a single seam: retrieval measures whether the answering chunk
reached the budget, groundedness measures what the model then did with it. Both
sides decide "the evidence arrived" with the SAME predicate,
`golden_set.chunk_satisfies`, so they cannot drift into two definitions of one
event -- and a groundedness failure can always be attributed to one side or the
other (the `evidence reached the model` split in its report).

Metadata is deliberately not measured by the retrieval harness: after
structured extraction, metadata questions are answered from a column and never
touch retrieval at all, so a retrieval metric structurally cannot score them.

The `-m` form is required for all three: `pyproject.toml` packages only `app*`, so
`evals` is not installed and file-path invocation drops cwd from `sys.path`.
