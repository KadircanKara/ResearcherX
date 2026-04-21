# ResearcherX — notes for Claude

Multi-agent research assistant. Pipeline: **Planner → parallel Searchers → Synthesizer (streamed) → Critic**. Orchestrated in `app/services/research_service.py`.

## Stack

- Backend: FastAPI (async) + SQLAlchemy 2.0 async + Alembic + Postgres
- LLM: **Groq** (`llama-3.3-70b-versatile`) via the OpenAI-compatible SDK. Provider is isolated to `app/llm/client.py` + `GROQ_*` env vars.
- Search: **DuckDuckGo** (`ddgs`). No API key.
- Streaming: in-process `asyncio.Queue` event bus (`app/services/event_bus.py`) → `sse-starlette` → `EventSource` in the client.
- Frontend: Next.js 15 App Router + TS + Tailwind.

## Layers

`api/v1/` → `services/` → `agents/` → `tools/` → `llm/` + `db/`, `schemas/`, `core/`. Keep layer boundaries clean: agents know about tools and the LLM, services know about agents + the bus + the db, api knows services.

## Non-obvious rules (fixed-bug knowledge — don't reintroduce)

- **All datetime columns must be `DateTime(timezone=True)`.** asyncpg rejects tz-aware datetimes against naive `TIMESTAMP WITHOUT TIME ZONE`. `_now()` returns `datetime.now(timezone.utc)`.
- **`run.status` is a plain `str` after SQLAlchemy round-trip**, because the column is `String(16)` not `Enum()`. Don't call `.value` on it — use `str(run.status)` or pass directly to pydantic.
- **`docker compose restart` does NOT re-read `env_file`.** Use `docker compose up -d --force-recreate <svc>` after editing `.env`. Code mounts auto-reload via uvicorn `--reload`; env changes do not.
- **No server-side fetches to the backend from Next.js.** Inside the frontend container, `localhost:8000` resolves to the frontend itself. `/research/[id]/page.tsx` only passes the id to a client component, which does the initial GET + SSE subscription. If you need SSR-side backend calls, add a second base URL (e.g. `INTERNAL_API_URL=http://backend:8000`) and switch on `typeof window`.
- **pydantic-settings decodes complex types BEFORE validators run.** CSV env vars into `list[str]` need `Annotated[list[str], NoDecode]` + a `@field_validator(mode="before")` that splits the string. See `cors_origins` in `app/core/config.py`.
- **DB port is intentionally not published** in `docker-compose.yml` — the dev host typically has its own Postgres on 5432. Backend reaches db via the docker network.

## Structured outputs

`app/llm/structured.py::parse_structured()` — JSON mode + schema pasted into the system prompt + retry-once-with-stricter-prompt + tolerant JSON extraction (strips code fences, slices to first balanced `{...}`). Keep it — Groq's Llama is reliable but even GPT-class models occasionally drop into reasoning prose.

`app/agents/searcher.py` degrades gracefully: if `parse_structured` raises, it returns a `SearchFinding` built from raw DDG snippets. Preserve this — one flaky response shouldn't fail the whole run.

## Provider swap-back

- `.env.example` keeps `ANTHROPIC_API_KEY` and `OPENROUTER_API_KEY` as preserved-but-unused slots.
- To flip: rewrite `app/llm/client.py` (and `structured.py` if the new SDK isn't OpenAI-compatible) + update env. Agent and service code does not change.
- **OpenRouter is not viable on the free tier for this app.** 50 req/day on unverified accounts; one full run is 5+ requests. `openrouter/free` also routes to models that reject `system` messages and `response_format`. We evaluated and rejected.

## Rate-limit budget (Groq free tier)

~30 req/min on llama-3.3-70b. One run ≈ 1 planner + N searchers + 1 synth + 1 critic. `max_parallel_searchers=3`, planner caps sub_queries at 3 (enforced by pydantic schema). OpenAI SDK `max_retries=5` handles 429 backoff automatically.

## Migrations

`make revision m="msg"` then `make migrate`. Initial revision is in `alembic/versions/`. If models change, autogenerate picks it up.

## Running

```bash
cp .env.example .env  # set GROQ_API_KEY
make up
make migrate          # first time only
```

Frontend on :3000, backend on :8000/docs.
