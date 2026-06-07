# ResearcherX — notes for Claude

Multi-agent research assistant. Pipeline: **Planner → parallel (Searcher → Planner-validation → retry) loops → Synthesizer (streamed) → Critic**. Orchestrated in `app/services/research_service.py`.

## Stack

- Backend: FastAPI (async) + SQLAlchemy 2.0 async + Alembic + Postgres
- LLM: **Groq free tier** (`llama-3.3-70b-versatile`) via the OpenAI-compatible SDK. Provider is isolated to `app/llm/client.py` + `LLM_*` env vars (`LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL`). Any OpenAI-compatible endpoint works by re-pointing env (see Provider swap).
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
- **The event bus has no replay — two orderings make the UI lossless anyway.** Service: `_record_step` BEFORE `bus.publish` (every published event is already persisted). UI (`run-stream.tsx`): subscribe to SSE FIRST, then GET the snapshot and seed state from `run.steps`, buffering live events until the seed lands. An event is therefore either in the snapshot or received live; GET-then-subscribe loses fast events (the plan lands <1s after run creation — found by e2e, plan section missing). Preserve both orderings. Related: the accepted search step's output is re-written post-validation (`_update_step_output`) so seeded findings carry final `validated`/`accepted_degraded` flags.
- **Run tasks are registered, not fire-and-forget** (`services/task_registry.py`). The registry + bus are in-process — **uvicorn must run a single worker**. SSE disconnect cancels an unwatched run only after a **10s grace window** (`UNWATCHED_CANCEL_GRACE_S` in `api/v1/research.py`): a page refresh closes the old EventSource before the new one connects, so zero-grace cancellation kills runs on every reload.
- **Startup auto-migrates and reaps orphans** (lifespan in `main.py`): programmatic `alembic upgrade head` (`db/migrate.py` — Config built WITHOUT alembic.ini on purpose; passing the ini makes env.py run `fileConfig`, which silences uvicorn's loggers), then PENDING/RUNNING runs are marked FAILED "interrupted by restart". Shutdown cancels registered tasks BEFORE disposing the engine — the CancelledError handlers still write final statuses.
- **Client-visible error text is generic by design.** `run.error`/SSE error events carry `"The research run failed. (ref: <run_id>)"`; validator fail-open reasons say only `"validator unavailable"`. Tracebacks/exception text stay in server logs. Don't "improve" client messages with `str(exc)`.
- **`ENVIRONMENT=prod` refuses dev fallbacks** (`Settings.validate_for_environment`): empty `LLM_API_KEY` or sqlite `DATABASE_URL` aborts startup.
- **Bus queues are bounded (2048, drop-oldest).** A normal run emits ~1.1k events, so the bound must stay above that. A drop only degrades a stuck consumer's live view — the snapshot seed restores on refresh.
- **Never add `rehype-raw` to the report renderer.** Report text is LLM/web-derived; react-markdown v9 escapes raw HTML by default and that default is the XSS policy.

## Planner validation loop

The planner does not pass garbage downstream. Per sub-query, `search_one()` in `research_service.py` runs: search → `PlannerAgent.validate()` (one structured call that both judges — on-topic? useful? non-empty? — and proposes a `revised_query` when invalid) → retry with the revised query. Rules:

- Validation belongs to the **planner** (a `validate()` method), not a fourth agent persona.
- Retry cap: `max_search_retries=2` (so ≤3 attempts). Retries also require a revised query that's non-empty and differs from the current one — no infinite loops.
- Cheap pre-check: empty finding (`no sources` / `"No results found."`) with no retry budget left is auto-invalid without an LLM call. An empty finding is never accepted as valid even if the model says so.
- Fail-open: if the validation call itself errors, treat the finding as valid (a flaky validator must not make the pipeline less robust than no validator).
- Degrade-don't-fail: after the cap, the best attempt is kept and marked `accepted_degraded=True`.
- Steps recorded as `StepKind.VALIDATE`; SSE events `validation` and `search_retry` feed the UI.

## Structured outputs

`app/llm/structured.py::parse_structured()` — JSON mode + schema pasted into the system prompt + retry-once-with-stricter-prompt + tolerant JSON extraction (strips `<think>...</think>` reasoning blocks, code fences, slices to first balanced `{...}`). Keep it — it costs nothing on Groq and is what keeps the client portable to noisier OpenAI-compatible endpoints (reasoning models emit `<think>` blocks; some endpoints reject `response_format`).

`response_format={"type": "json_object"}` is best-effort: some OpenAI-compatible endpoints reject it, so `_one_shot` falls back without it (and remembers). The schema-in-prompt is the real guarantor of JSON.

`app/agents/searcher.py` degrades gracefully: if `parse_structured` raises, it returns a `SearchFinding` built from raw DDG snippets. Preserve this — one flaky response shouldn't fail the whole run.

## Provider swap

- Groq is the only configured provider (dev + prod); `config.py` defaults match `.env.example`. The client is the plain OpenAI SDK, so flipping to any OpenAI-compatible endpoint is **env-only** (`LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL`) — no code changes. To go to a non-OpenAI SDK, rewrite `app/llm/client.py` (and `structured.py` if needed); agent and service code does not change.
- Local Ollama was the dev default until 2026-06, then removed entirely (no usable GPU on the dev machine; CPU inference is too slow for the multi-call pipeline). Don't reintroduce local-model scaffolding — no Ollama compose service, no `extra_hosts`, no local-model env blocks.
- **OpenRouter is not viable on the free tier for this app.** 50 req/day on unverified accounts; one full run is 5+ requests. `openrouter/free` also routes to models that reject `system` messages and `response_format`. We evaluated and rejected.

## Rate-limit budget (Groq free tier)

- ~30 req/min on `llama-3.3-70b-versatile`. One run ≈ 1 planner + N searchers + N–3N validations + 1 synth + 1 critic calls (validation calls are small, `max_tokens=400`) — the validation loop makes runs noticeably heavier than the pre-validation pipeline, so keep `max_parallel_searchers=3` and the sub_queries cap of 3 (pydantic schema).
- `LLM_MAX_RETRIES=5` (the config default) lets the SDK absorb 429s with exponential backoff. A dozen 429s in a single run is normal operation, not an error.
- **The binding budget is tokens/day, not req/min**: 100k TPD on the free tier, and a measured full run costs ~12.5k tokens (2026-06-07: 8 runs exhausted the day). `global_daily_run_cap=10` is derived from that math — don't raise it without a paid tier. TPD exhaustion mid-run surfaces as `openai.RateLimitError` → the run fails with the sanitized message (correct behavior, verified live).

## Migrations

`make revision m="msg"` to autogenerate. Migrations apply automatically at backend startup; `make migrate` remains as a manual escape hatch. Initial revision is in `alembic/versions/`.

## Tests

`make test` (runs pytest inside the backend container; dev deps are baked into the Dockerfile `dev` target). `backend/tests/conftest.py` forces a throwaway sqlite DB and unroutable LLM env **before any app import** — tests never touch postgres or the network; all agents are faked. It also resets the in-memory rate-limiter storage per test (module-global; shared per-IP windows otherwise leak across tests). `tests/__init__.py` must exist (cross-module fake imports).

## Running

```bash
cp .env.example .env  # set LLM_API_KEY (free key: https://console.groq.com/keys)
make up
```

Frontend on :3000, backend on :8000/docs. Remember: after editing `.env`, `docker compose up -d --force-recreate backend` (restart won't re-read it).

## Prod stack (local or box)

`make prod-up` / `prod-down` / `prod-logs` — `docker-compose.prod.yml` under project name `researcherx-prod`, so it coexists with the dev stack. Requires `POSTGRES_PASSWORD`, `LLM_API_KEY`, `OWNER_API_KEY` exported (SSM on the box). Caddy on :80 is the only published port (`/v1/*` → backend with `flush_interval -1` for SSE, rest → frontend). Dockerfiles are multi-target: dev compose builds `target: dev` (root, reload, dev deps); prod builds the default target (non-root, no source mounts, backend `--workers 1` — **load-bearing**, see security.py docstring). The prod frontend image inlines `NEXT_PUBLIC_API_BASE=""` at build → same-origin `/v1/...` URLs through Caddy, domain-agnostic.

## Rate limits / auth (D3)

`app/core/security.py`: per-IP moving windows (`rate_limit_runs` 3/hour;10/day on POST, `rate_limit_reads` on GETs) keyed on first `X-Forwarded-For` hop; global daily cap = count of today's `research_runs` rows (DB is the counter — restart-proof, no extra table); `X-API-Key` == `owner_api_key` bypasses all (constant-time). Built on `limits` directly, NOT slowapi — slowapi's `exempt_when` takes no request argument, so an owner bypass can't be expressed through it. In-memory limiter storage is valid ONLY because the backend is single-worker.
