# ResearcherX — notes for Claude

Multi-agent research assistant. Pipeline: **Planner → parallel (Searcher → Planner-validation → retry) loops → Synthesizer (streamed) → Critic**. Orchestrated in `app/services/research_service.py`.

## Stack

- Backend: FastAPI (async) + SQLAlchemy 2.0 async + Alembic + Postgres
- LLM: **local Ollama** (default model `gemma4`) via the OpenAI-compatible SDK. Provider is isolated to `app/llm/client.py` + `LLM_*` env vars (`LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL`). Any OpenAI-compatible endpoint works by re-pointing env.
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
- **New SSE event types must be registered in TWO places in `frontend/src/components/run-stream.tsx`**: the `switch` AND the `addEventListener` kind list. `EventSource` only fires listeners for named events — an unlisted type is silently dropped.
- **`StepKind`/`RunStatus` are Python-side `StrEnum`s over `String(16)` columns** — adding a member (e.g. `VALIDATE = "validate"`) needs **no alembic migration**; autogenerate will correctly produce an empty diff.

## Planner validation loop

The planner does not pass garbage downstream. Per sub-query, `search_one()` in `research_service.py` runs: search → `PlannerAgent.validate()` (one structured call that both judges — on-topic? useful? non-empty? — and proposes a `revised_query` when invalid) → retry with the revised query. Rules:

- Validation belongs to the **planner** (a `validate()` method), not a fourth agent persona.
- Retry cap: `max_search_retries=2` (so ≤3 attempts). Retries also require a revised query that's non-empty and differs from the current one — no infinite loops.
- Cheap pre-check: empty finding (`no sources` / `"No results found."`) with no retry budget left is auto-invalid without an LLM call. An empty finding is never accepted as valid even if the model says so.
- Fail-open: if the validation call itself errors, treat the finding as valid (a flaky validator must not make the pipeline less robust than no validator).
- Degrade-don't-fail: after the cap, the best attempt is kept and marked `accepted_degraded=True`.
- Steps recorded as `StepKind.VALIDATE`; SSE events `validation` and `search_retry` feed the UI.

## Structured outputs

`app/llm/structured.py::parse_structured()` — JSON mode + schema pasted into the system prompt + retry-once-with-stricter-prompt + tolerant JSON extraction (strips `<think>...</think>` reasoning blocks, code fences, slices to first balanced `{...}`). Keep it — local models drop into reasoning prose far more often than cloud GPT-class models; deepseek-r1 *always* emits `<think>` blocks.

`response_format={"type": "json_object"}` is best-effort: some Ollama versions/models reject it via the OpenAI-compat layer, so `_one_shot` falls back without it (and remembers). The schema-in-prompt is the real guarantor of JSON.

`app/agents/searcher.py` degrades gracefully: if `parse_structured` raises, it returns a `SearchFinding` built from raw DDG snippets. Preserve this — one flaky response shouldn't fail the whole run.

## Provider swap

- Default is local Ollama; the client is the plain OpenAI SDK, so flipping to any OpenAI-compatible cloud provider (e.g. Groq: `LLM_BASE_URL=https://api.groq.com/openai/v1` + real key) is **env-only** — no code changes.
- `.env.example` keeps `ANTHROPIC_API_KEY` and `OPENROUTER_API_KEY` as preserved-but-unused slots. To go to a non-OpenAI SDK, rewrite `app/llm/client.py` (and `structured.py` if needed). Agent and service code does not change.
- **OpenRouter is not viable on the free tier for this app.** 50 req/day on unverified accounts; one full run is 5+ requests. `openrouter/free` also routes to models that reject `system` messages and `response_format`. We evaluated and rejected.

## Local model notes

- No rate limits, but one Ollama instance serializes requests (`OLLAMA_NUM_PARALLEL` defaults low) and parallel calls contend for RAM/VRAM — that's what `max_parallel_searchers=3` budgets now, not quotas. Planner still caps sub_queries at 3 (pydantic schema).
- One run ≈ 1 planner + N searchers + N–3N validations + 1 synth + 1 critic calls. Validation calls are small (`max_tokens=400`).
- `llm_max_retries=2` on the SDK client covers transient blips while a model cold-loads into memory.
- **Ollama runs on the HOST, not in compose** (macOS Docker has no GPU passthrough — containerized would be CPU-only). Backend reaches it via `host.docker.internal:11434`; `extra_hosts: host-gateway` makes that work on Linux too. An opt-in `ollama` compose profile exists for Linux/GPU setups (`docker compose --profile ollama up` + `LLM_BASE_URL=http://ollama:11434/v1`).

## Migrations

`make revision m="msg"` then `make migrate`. Initial revision is in `alembic/versions/`. If models change, autogenerate picks it up.

## Running

```bash
ollama pull gemma4    # host-run Ollama; any model from `ollama list` works
cp .env.example .env  # defaults already point at host Ollama + gemma4
make up
make migrate          # first time only
```

Frontend on :3000, backend on :8000/docs. Remember: after editing `.env`, `docker compose up -d --force-recreate backend` (restart won't re-read it).
