"""A deliberately small, local-disk RAG system.

No database. The corpus is files under one directory: paper metadata and
chunk text as JSON, chunk vectors as a single `.npy` matrix, and the BM25
index as `bm25s`'s own on-disk format. Retrieval is the three stages the
operator specified and nothing else:

    dense (numpy cosine) + BM25  ->  weighted RRF  ->  Cohere rerank

What this deliberately does NOT carry, against `app/services/chat_service.py`
(see `docs/rag-hardening/PLAN.md`): the single-paper gate switch, the two
single-paper cut policies, the per-paper representation floor, the
`PARTITION BY paper_id` candidate guarantee, the scope widener, and the
dense-only kill switch. Each exists in the database path for a measured
reason recorded in CLAUDE.md; none is reintroduced here without the
measurement that justified it.
"""
