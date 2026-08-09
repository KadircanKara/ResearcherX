# Evaluation harnesses

Two harnesses, measuring two things that fail independently.

- **`retrieval/`** — does semantic search surface the chunk that answers a
  question? Run: `docker compose exec -T backend python -m evals.retrieval.run_eval`
- **`metadata/`** — did extraction get each paper's authors, year, and venue
  right, and did it invent any it shouldn't have? Run:
  `docker compose exec -T backend python -m evals.metadata.run_eval`

Both read the **live dev database** and never write to it.

Metadata is deliberately not measured by the retrieval harness: after
structured extraction, metadata questions are answered from a column and never
touch retrieval at all, so a retrieval metric structurally cannot score them.

The `-m` form is required for both: `pyproject.toml` packages only `app*`, so
`evals` is not installed and file-path invocation drops cwd from `sys.path`.
