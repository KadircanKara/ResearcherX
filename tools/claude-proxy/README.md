# claude-proxy — run the dev pipeline on your Claude Code login

An OpenAI-compatible `/v1/chat/completions` endpoint that answers by shelling
out to the Claude Code CLI. Dev only.

```
backend (container) --HTTP--> host:8787 --subprocess--> claude -p
```

## Why a proxy and not a transport in `app/llm/client.py`

The backend runs in Docker with no node runtime and no `~/.claude`
credentials; getting the CLI in there means installing node in the image and
mounting your login into a container. The provider-swap contract in
`CLAUDE.md` is deliberately env-only, and this keeps it that way: **no
application code changes**, and the production path is untouched.

Note the tension with `CLAUDE.md`'s rule against `host.docker.internal`
coupling to a host-installed model server. That rule is about the Ollama/GPU
case, where the coupling was to be permanent and load-bearing. This is a
commented-out dev override in `.env` with `docker-compose.yml` unchanged.

## Use

```bash
make claude-proxy                 # host terminal, keep it running
```

Then uncomment the four `LLM_*` lines in `.env` (the block is in
`.env.example`) and recreate the backend — `restart` does not re-read
`.env`:

```bash
docker compose up -d --force-recreate backend
```

Verify:

```bash
curl -s localhost:8787/healthz
curl -s localhost:8787/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"haiku","messages":[{"role":"user","content":"Say exactly: pong"}]}'
```

Options (flag or env var): `--port`/`CLAUDE_PROXY_PORT` (8787),
`--model`/`CLAUDE_PROXY_MODEL` (`opus`), `--concurrency`/
`CLAUDE_PROXY_CONCURRENCY` (4), `--timeout`/`CLAUDE_PROXY_TIMEOUT` (600s),
`--binary`/`CLAUDE_PROXY_BIN` (`claude`). Standard library only — no venv, no
install step.

`LLM_MODEL` is honoured when it names a Claude model (`opus`, `sonnet`,
`haiku`, `fable`, `claude-*`); anything else — a `.env` still set to
`gpt-4.1-mini` — falls back to the proxy default instead of handing the CLI a
model it would reject.

Evals and the groundedness judge come along for free: they read the same
`LLM_*` settings. Point `JUDGE_BASE_URL`/`JUDGE_API_KEY`/`JUDGE_MODEL` at the
proxy too if you want the judge on Claude Code — but a judge sharing a vendor
with the system under test is exactly what those separate vars exist to
avoid.

## Flags the CLI is invoked with, and why

```
claude -p --restricted --tools "" --strict-mcp-config --max-turns 1 \
       --model <model> --system-prompt <system> \
       --output-format json | stream-json --include-partial-messages --verbose
```

- **`--tools ""` is load-bearing.** Without it every request pays for the full
  Claude Code tool schema. Measured 2026-09-07 on one "say pong" request:
  **11,844** input tokens with tools, **517** without.
- `--restricted` drops the command-running tools and ignores user/project/local
  settings; `--strict-mcp-config` skips MCP servers. This must behave like a
  plain model endpoint, not an agent with a filesystem.
- `--max-turns 1`: one request, one answer, no agentic loop.
- **The subprocess runs in an empty temp directory**, created at startup. Run
  it from the repo instead and the CLI auto-discovers ResearcherX's own
  `CLAUDE.md` and prepends it to every request the app makes.
- **`--bare` cannot be used.** It looks ideal (skips hooks, memory, CLAUDE.md
  discovery) but forces `ANTHROPIC_API_KEY` and never reads the OAuth login —
  it answers `Not logged in · Please run /login`, which defeats the point.

## Deliberate limits

- **`max_tokens` is accepted and ignored** — the CLI exposes no equivalent. A
  caller relying on a ceiling (`chat_answer_max_tokens`, the retrieval
  planner's 600) gets none here. Reasoning tokens are billed but not capped.
- **`temperature`, `top_p`, `stop`, `n` are accepted and ignored** for the
  same reason.
- **`response_format={"type":"json_object"}` is honoured by appending a
  JSON-only instruction to the system prompt**, not by a real JSON mode. The
  model still fences its output sometimes; `structured.py::_extract_json`
  strips fences and slices to the first balanced object, which is what makes
  this work — verified against that function, not against a bare
  `json.loads`. The schema-in-prompt remains the real guarantor.
- **Thinking deltas are dropped, never forwarded.** The CLI emits
  `thinking_delta` events by default; forwarding them would stream reasoning
  into a synthesised report.
- **Message history is rendered as a labelled text transcript** (`USER:` /
  `ASSISTANT:`) when there is more than one non-system message. Verified to
  carry context across turns.
- **Latency floor is ~1.5-3s per call** (process spawn + CLI startup), on top
  of model time. One research run is 10+ calls.
- **Embeddings are not proxied.** Claude has no embeddings endpoint; leave
  `EMBEDDING_*` on Ollama or OpenAI.
- **Multimodal content parts are dropped** — text parts only.

## Context freshness, and the preamble that cannot be removed

Every call is a **fresh session**. There is no `--resume`, no session
persistence, no auto-memory, and the empty temp cwd keeps this repo's own
`CLAUDE.md` out of the prompt. Verified: one call told the model a secret
word, the next call asked for it back and got `NONE`.

What is *not* removable is a fixed preamble the Agent SDK wraps around the
`--system-prompt` on every call — roughly 435 input tokens:

```
You are a Claude agent, built on Anthropic's Claude Agent SDK.<our system prompt>

# Environment
 - Primary working directory: ... - Platform: darwin - OS Version: ...
You are powered by the model named ... knowledge cutoff ...

# userEmail
The user's email address is <the logged-in account>. ...

Today's date is ...
```

So the model knows the machine, the date, and the account email of whoever
is logged in — on a request the app believes carries only its own system
prompt. Measured 2026-09-07, and every way of stripping it failed:

| Attempt | Result |
|---|---|
| `--exclude-dynamic-system-prompt-sections` | identical 435 tokens, still present (the flag is ignored with `--system-prompt`) |
| `--setting-sources "" --no-session-persistence` | no change |
| `CLAUDE_CODE_SIMPLE=1` (what `--bare` sets) | `Not logged in · Please run /login` |
| A system-prompt fence telling the model to ignore the boilerplate | haiku and opus both still answered with the email |
| A sanitized `HOME` (copy of `.claude.json` with the account fields blanked) | `Not logged in`, with and without a symlink to the real `~/.claude` |

`--bare` is the only thing that removes it, and it hard-requires
`ANTHROPIC_API_KEY` — which bills API credits rather than the Claude Code
subscription this proxy exists to use. Accepted as a known limit: the
preamble is constant, carries no conversation history, and this pipeline
sends research questions and paper chunks.

## Errors

A CLI failure whose text mentions a rate/usage limit or quota is returned as
**429**, so `ProviderPool` treats it like any other exhausted provider; other
failures are 502, a timeout is 504. Streaming headers are only sent once the
first token arrives, so a failure before that is still a real HTTP status
rather than an empty 200. A failure *after* the first token closes the stream
without `[DONE]` — the same shape as the mid-stream deaths
`research_service` already retries via `rotate_current()`.

## Linux

`host.docker.internal` resolves out of the box on Docker Desktop (macOS,
Windows). On Linux add to the backend service:

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```
