# ResearcherX

Autonomous multi-agent research assistant. Ask a question, watch specialized agents plan, search, synthesize, and verify citations in real time — running entirely on local models.

The planner doesn't pass garbage downstream: it validates every searcher result (on-topic? complete/useful? non-empty?) and retries with a revised query when a result is off-topic, empty, or unhelpful (capped, then best-effort).

## Stack

- **Backend:** FastAPI (async) + SQLAlchemy 2.0 async + Alembic + Postgres
- **LLM:** Local models via [Ollama](https://ollama.com) (gemma4, deepseek-r1, llama3.1, …) through its OpenAI-compatible API. Provider layer is a single file — any OpenAI-compatible endpoint (e.g. Groq) works by re-pointing `LLM_*` env vars.
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
                                │  LLM       ─▶ Ollama (local)       │
                                │  DB        ─▶ Postgres (runs,      │
                                │              steps, citations)     │
                                └────────────────────────────────────┘
```

Layers (backend/app):

- `api/v1/` — route handlers, request/response validation only
- `services/` — orchestration, use cases (one call per API request)
- `agents/` — specialized LLM roles (planner, searcher, synthesizer, critic)
- `tools/` — typed pure-function wrappers around side-effectful ops
- `llm/` — Ollama (OpenAI-compatible) client + structured-output helper
- `db/` — SQLAlchemy models, async session factory
- `schemas/` — Pydantic DTOs (API boundary)
- `core/` — config, logging

## Quick start

```bash
# 1. Run Ollama on the host (https://ollama.com) and pull a model:
ollama pull gemma4    # or deepseek-r1:8b, llama3.1, qwen3, ...

# 2. Configure + start. Defaults already point at host Ollama + gemma4;
#    set LLM_MODEL to any model from `ollama list`.
cp .env.example .env
make up
```

Frontend: http://localhost:3000 — Backend: http://localhost:8000/docs

First run needs DB migrations:

```bash
make migrate
```

## Scaling notes

This scaffold is single-process. Two natural upgrade points:

1. **Run dispatch** — the per-run event queue is in-process (`services/event_bus.py`). For multi-replica deploy, swap for Redis pub/sub.
2. **Agent work** — orchestration runs inline on the request task. For heavy concurrency, move `research_service.run()` into an Arq/Celery worker and have the SSE endpoint subscribe instead of orchestrate.
