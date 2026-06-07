# ResearcherX

Autonomous multi-agent research assistant. Ask a question, watch specialized agents plan, search, synthesize, and verify citations in real time.

The planner doesn't pass garbage downstream: it validates every searcher result (on-topic? complete/useful? non-empty?) and retries with a revised query when a result is off-topic, empty, or unhelpful (capped, then best-effort).

## Stack

- **Backend:** FastAPI (async) + SQLAlchemy 2.0 async + Alembic + Postgres
- **LLM:** [Groq](https://groq.com) free tier (`llama-3.3-70b-versatile`) through its OpenAI-compatible API. Provider layer is a single file — any OpenAI-compatible endpoint works by re-pointing `LLM_*` env vars.
- **Search:** DuckDuckGo via `ddgs` (no API key)
- **Frontend:** Next.js 15 (App Router) + TypeScript + Tailwind
- **Infra:** Docker Compose

## Architecture

```
┌──────────────────────┐        ┌────────────────────────────────────┐
│  Next.js frontend    │        │  FastAPI backend                   │
│  ─ query form        │  HTTP  │  ─ /api/v1/research  (POST create) │
│  ─ run streaming UI  │◀──────▶│  ─ /api/v1/research/{id}/events    │
│                      │  SSE   │      (SSE stream)                  │
└──────────────────────┘        │                                    │
                                │  Services ─▶ Supervisor Agent      │
                                │              ├─ Planner            │
                                │              ├─ Searchers (N)      │
                                │              │   ↑↓ validate/retry │
                                │              ├─ Synthesizer        │
                                │              └─ Critic             │
                                │  Tools     ─▶ web_search (DDG)     │
                                │  LLM       ─▶ Groq (free)          │
                                │  DB        ─▶ Postgres (runs,      │
                                │              steps, citations)     │
                                └────────────────────────────────────┘
```

Layers (backend/app):

- `api/v1/` — route handlers, request/response validation only
- `services/` — orchestration, use cases (one call per API request)
- `agents/` — specialized LLM roles (planner, searcher, synthesizer, critic)
- `tools/` — typed pure-function wrappers around side-effectful ops
- `llm/` — OpenAI-compatible client (Groq by default) + structured-output helper
- `db/` — SQLAlchemy models, async session factory
- `schemas/` — Pydantic DTOs (API boundary)
- `core/` — config, logging

## Quick start

```bash
cp .env.example .env
# edit .env and set LLM_API_KEY (free key: https://console.groq.com/keys)
make up
```

Frontend: http://localhost:3000 — Backend: http://localhost:8000/docs

Migrations run automatically at backend startup — a fresh database needs no
manual step. Run the backend test suite with `make test`.

## Scaling notes

This scaffold is single-process. Two natural upgrade points:

1. **Run dispatch** — the per-run event queue is in-process (`services/event_bus.py`). For multi-replica deploy, swap for Redis pub/sub.
2. **Agent work** — runs execute as in-process asyncio tasks (tracked in `services/task_registry.py`, cancelled on viewer disconnect and graceful shutdown). For heavy concurrency or multiple replicas, move them into an Arq/Celery worker and have the SSE endpoint subscribe instead of orchestrate. Until then the backend must run as a **single uvicorn worker**.
