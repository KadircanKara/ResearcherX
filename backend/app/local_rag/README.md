# Local RAG

A deliberately small retrieval system: **no database**, three stages, and
the parameters set on 2026-09-06.

```
dense (numpy cosine)  ┐
                      ├─ weighted RRF (0.5 / 0.5, k=60) ─ top 50 ─ Cohere rerank ─ top 10
BM25 (bm25s)          ┘
```

## Run it

```bash
docker compose exec -T backend python -m app.local_rag.cli ingest paper.pdf notes.md
docker compose exec -T backend python -m app.local_rag.cli ask "what is revisit time?"
```

Outside the container, from `backend/` with an embedding endpoint reachable:

```bash
python -m app.local_rag.cli ingest ~/papers/*.pdf --root ./data/local_rag
python -m app.local_rag.cli ask "what optimiser did they use?" --top-n 5 --no-answer
```

`-m` is required: `pyproject.toml` packages only `app*`, so file-path
invocation drops the working directory from `sys.path`.

## What is on disk

```
<LOCAL_RAG_DIR>/
  manifest.json    embedding model, dimension, chunk count, papers
  chunks.jsonl     one chunk per line, IN VECTOR ROW ORDER
  embeddings.npy   float32 (n_chunks, dim)
  bm25/            bm25s's own index
```

Row `i` of `embeddings.npy`, line `i` of `chunks.jsonl` and document `i` of
the BM25 index are the same chunk. Nothing downstream can detect a break in
that alignment — it returns a real score attached to the wrong text — so
`LocalStore.save` refuses a vector matrix whose row count disagrees with
the chunk list, and `load` re-checks on the way back in.

Vectors go through `np.save` rather than JSON: 4.6k chunks at 768 float32 is
~14MB of binary against ~70MB of JSON that also loses the dtype.

## Parameters

| Parameter | Value | Where |
|---|---|---|
| dense weight / sparse weight | 0.5 / 0.5 | `SearchParams` |
| RRF `k` | 60 | `SearchParams` |
| distance gate | 0.75 cosine | `SearchParams.similarity_threshold` |
| dense pool / sparse pool | 100 / 100 | `SearchParams` |
| rerank candidates | 50 | `SearchParams.rerank_candidates` |
| rerank top-n | 10 | `SearchParams.rerank_top_n` |
| rerank model | `rerank-v3.5`, Cohere defaults otherwise | `COHERE_RERANK_MODEL` |
| chunking | 384 words, 48 overlap | imported from `paper_ingest_service` |

Equal weights and `k=60` belong together. A sparse-only chunk reaches the
budget when `w_sparse·(k + depth) > w_dense·(k + 1)`; at 0.5/0.5 that is
`depth > 1`, true at any `k`. It is the 0.7/0.3 asymmetry that forced `k`
down to 30 on the database path, and moving one of these two without the
other re-opens that.

## What it does NOT do

Against `app/services/chat_service.py`, deliberately absent: the
single-paper gate switch, both single-paper cut policies
(`intra_paper_delta`, `intra_paper_rank_window`), the per-paper
representation floor, the `PARTITION BY paper_id` candidate guarantee, the
scope widener, and the dense-only kill switch. Each was measured into
existence for the database path and is recorded in `CLAUDE.md`; none should
come back here without the measurement that justified it.

Kept, because they are correctness rather than mechanics: the system prompt
contract, and the citation post-pass in production's own order —
`expand_grouped_citations` → `strip_misattributed_citations` →
`renumber_citations`. Renumbering assigns 1..N by first appearance, so a
marker stripped after it would leave a citation pointing at a claim that no
longer cites it.

## Degradation

| Missing | Effect |
|---|---|
| `COHERE_API_KEY` | rerank skipped, fused RRF order kept |
| Cohere errors or times out | same — `rerank()` returns `None`, never raises |
| no BM25 match | dense arm alone decides |
| nothing clears either arm | empty catalog, and the prompt's refusal branch answers |
| `EMBEDDING_MODEL` changed since ingest | `retrieve()` refuses rather than comparing two vector spaces |

## Tests

```bash
pytest tests/test_local_rag_*.py -q
```

No network: the embedding call is injected, and the Cohere client is
exercised through `httpx.MockTransport`.
