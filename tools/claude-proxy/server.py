"""OpenAI-compatible chat-completions endpoint backed by the Claude Code CLI.

Dev-only. Runs on the HOST (the `claude` binary and its OAuth login live
there, not in the backend container) and exposes just enough of the OpenAI
API for `app/llm/client.py` to talk to it unchanged:

    backend (container) --HTTP--> host:8787 --subprocess--> claude -p

Point the dev env at it and nothing else changes:

    LLM_BASE_URL=http://host.docker.internal:8787/v1
    LLM_API_KEY=claude-code          # ignored, but Settings requires non-empty
    LLM_MODEL=opus
    LLM_FALLBACKS=[]

Why a proxy instead of a transport inside `app/llm/client.py`: the backend
runs in Docker with no node runtime and no `~/.claude` credentials, and the
provider-swap contract in CLAUDE.md is deliberately env-only. This keeps the
production code path untouched.

Deliberate limits (documented, not oversights):

* `max_tokens`, `temperature`, `top_p`, `stop` and friends are ACCEPTED AND
  IGNORED — the CLI exposes no equivalent. A caller that depends on a token
  ceiling (e.g. `chat_answer_max_tokens`) gets no ceiling here.
* `response_format={"type": "json_object"}` is honoured by appending a
  JSON-only instruction to the system prompt, not by a real JSON mode. The
  schema-in-prompt in `app/llm/structured.py` stays the real guarantor.
* Thinking blocks are dropped, never forwarded: the CLI emits
  `thinking_delta` events by default and they are not part of the answer.
* Embeddings are NOT proxied. Claude exposes no embeddings endpoint; leave
  `EMBEDDING_*` pointed at OpenAI or the local Ollama service.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# `--tools ""` is load-bearing, not tidiness: without it every call pays for
# the full Claude Code tool schema. Measured 2026-09-07 on one "say pong"
# request — 11,844 input tokens with tools, 517 without.
BASE_FLAGS = [
    "-p",
    "--restricted",
    "--tools",
    "",
    "--strict-mcp-config",
    "--max-turns",
    "1",
]

DEFAULT_SYSTEM = "You are a helpful assistant."
JSON_INSTRUCTION = "Respond with a single JSON object and nothing else — no prose, no code fences."

# The CLI reports an exhausted plan/quota in prose on the result line. Mapping
# it to 429 lets the pool's failover treat it like any other provider quota.
_RATE_LIMIT_MARKERS = ("rate limit", "usage limit", "quota", "too many requests")


class Config:
    def __init__(self, args: argparse.Namespace) -> None:
        self.binary = args.binary
        self.model = args.model
        self.timeout = args.timeout
        self.cwd = args.cwd
        self.semaphore = threading.BoundedSemaphore(args.concurrency)


def resolve_model(requested: object, default: str) -> str:
    """Use the requested model only when it names a Claude one.

    A dev `.env` left on `gpt-4.1-mini` should degrade to the proxy default
    rather than handing the CLI a model it will reject.
    """
    if not isinstance(requested, str) or not requested:
        return default
    name = requested.strip().lower()
    if name in {"opus", "sonnet", "haiku", "fable", "default", "opusplan"}:
        return name
    if name.startswith("claude"):
        return name
    return default


def render_messages(messages: list[dict]) -> tuple[str, str]:
    """Flatten an OpenAI message array into (system_prompt, user_prompt).

    Every system message is concatenated into `--system-prompt`. A lone user
    message is passed through verbatim; anything longer is rendered as a
    labelled transcript, which is the shape the CLI's text input can carry.
    """
    systems: list[str] = []
    turns: list[tuple[str, str]] = []
    for message in messages:
        role = message.get("role", "user")
        content = message.get("content")
        if isinstance(content, list):
            # Content parts: keep the text ones, drop images (unsupported here).
            content = "".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        if not isinstance(content, str):
            content = "" if content is None else str(content)
        if role == "system":
            systems.append(content)
        else:
            turns.append((role, content))

    system = "\n\n".join(s for s in systems if s.strip()) or DEFAULT_SYSTEM

    if len(turns) == 1 and turns[0][0] == "user":
        user = turns[0][1]
    else:
        user = "\n\n".join(f"{role.upper()}: {content}" for role, content in turns)
    return system, user


def build_command(config: Config, model: str, system: str, stream: bool) -> list[str]:
    command = [config.binary, *BASE_FLAGS, "--model", model, "--system-prompt", system]
    if stream:
        command += ["--output-format", "stream-json", "--include-partial-messages", "--verbose"]
    else:
        command += ["--output-format", "json"]
    return command


def _completion_envelope(model: str) -> dict:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "created": int(time.time()),
        "model": model,
    }


def _usage(raw: dict | None) -> dict:
    raw = raw or {}
    prompt = int(raw.get("input_tokens") or 0)
    prompt += int(raw.get("cache_creation_input_tokens") or 0)
    prompt += int(raw.get("cache_read_input_tokens") or 0)
    completion = int(raw.get("output_tokens") or 0)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }


class ProxyError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _classify(text: str) -> ProxyError:
    lowered = text.lower()
    if any(marker in lowered for marker in _RATE_LIMIT_MARKERS):
        return ProxyError(429, text)
    return ProxyError(502, text)


def run_once(config: Config, command: list[str], prompt: str) -> dict:
    """Non-streaming call: one `claude -p --output-format json` invocation."""
    with config.semaphore:
        completed = subprocess.run(
            command,
            input=prompt,
            capture_output=True,
            text=True,
            cwd=config.cwd,
            timeout=config.timeout,
        )
    if completed.returncode != 0:
        raise _classify(completed.stderr.strip() or f"claude exited {completed.returncode}")
    try:
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise ProxyError(502, f"unparseable CLI output: {exc}") from exc
    if payload.get("is_error"):
        raise _classify(str(payload.get("result") or payload.get("subtype") or "claude error"))
    return payload


def iter_stream(config: Config, command: list[str], prompt: str):
    """Yield ("delta", text) then ("done", result_payload) from stream-json.

    Only `text_delta` events are forwarded — `thinking_delta` is the model's
    reasoning, not its answer, and forwarding it would put `<think>`-shaped
    noise into a synthesised report.
    """
    with config.semaphore:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=config.cwd,
        )
        assert process.stdin and process.stdout and process.stderr
        try:
            process.stdin.write(prompt)
            process.stdin.close()
            result: dict | None = None
            for line in process.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("type") == "stream_event":
                    inner = event.get("event") or {}
                    delta = inner.get("delta") or {}
                    if (
                        inner.get("type") == "content_block_delta"
                        and delta.get("type") == "text_delta"
                    ):
                        text = delta.get("text")
                        if text:
                            yield "delta", text
                elif event.get("type") == "result":
                    result = event
            process.wait(timeout=config.timeout)
            if process.returncode != 0:
                raise _classify(
                    process.stderr.read().strip() or f"claude exited {process.returncode}"
                )
            if result is None:
                raise ProxyError(502, "stream ended without a result event")
            if result.get("is_error"):
                raise _classify(str(result.get("result") or "claude error"))
            yield "done", result
        finally:
            if process.poll() is None:
                process.kill()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    # No socket timeout by default: a client that announces a large body and
    # then stalls would pin a worker thread forever. Same guard, same reason,
    # as latex-compiler/app.py.
    timeout = 30

    config: Config  # injected in main()

    def log_message(self, fmt: str, *args) -> None:  # noqa: A002 - stdlib signature
        sys.stderr.write("[claude-proxy] %s\n" % (fmt % args))

    # -- helpers ---------------------------------------------------------

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: int, message: str) -> None:
        self._send_json(status, {"error": {"message": message, "type": "claude_proxy_error"}})

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            raise ProxyError(400, "empty request body")
        try:
            return json.loads(self.rfile.read(length))
        except ValueError as exc:
            raise ProxyError(400, f"invalid JSON body: {exc}") from exc

    # -- routes ----------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - stdlib signature
        if self.path.rstrip("/") in ("/v1/models", "/models"):
            self._send_json(
                200,
                {
                    "object": "list",
                    "data": [
                        {
                            "id": self.config.model,
                            "object": "model",
                            "owned_by": "claude-code",
                            "created": int(time.time()),
                        }
                    ],
                },
            )
            return
        if self.path.rstrip("/") in ("/healthz", "/v1/healthz"):
            self._send_json(200, {"status": "ok", "model": self.config.model})
            return
        self._send_error(404, f"no route for {self.path}")

    def do_POST(self) -> None:  # noqa: N802 - stdlib signature
        if self.path.rstrip("/") not in ("/v1/chat/completions", "/chat/completions"):
            self._send_error(404, f"no route for {self.path}")
            return
        try:
            body = self._read_body()
            messages = body.get("messages")
            if not isinstance(messages, list) or not messages:
                raise ProxyError(400, "messages must be a non-empty array")
            model = resolve_model(body.get("model"), self.config.model)
            system, prompt = render_messages(messages)
            if (body.get("response_format") or {}).get("type") == "json_object":
                system = f"{system}\n\n{JSON_INSTRUCTION}"
            command = build_command(self.config, model, system, stream=bool(body.get("stream")))
            if body.get("stream"):
                self._serve_stream(command, prompt, model)
            else:
                self._serve_once(command, prompt, model)
        except ProxyError as exc:
            self._send_error(exc.status, exc.message)
        except subprocess.TimeoutExpired:
            self._send_error(504, f"claude did not answer within {self.config.timeout}s")
        except Exception as exc:  # noqa: BLE001 - dev proxy: surface everything
            self._send_error(500, f"{type(exc).__name__}: {exc}")

    def _serve_once(self, command: list[str], prompt: str, model: str) -> None:
        payload = run_once(self.config, command, prompt)
        self._send_json(
            200,
            {
                **_completion_envelope(model),
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": payload.get("result") or ""},
                        "finish_reason": "stop",
                    }
                ],
                "usage": _usage(payload.get("usage")),
            },
        )

    def _serve_stream(self, command: list[str], prompt: str, model: str) -> None:
        envelope = _completion_envelope(model)
        started = False
        try:
            for kind, value in iter_stream(self.config, command, prompt):
                if not started:
                    # Headers go out only once the CLI produced something, so a
                    # failure before the first token can still be a real HTTP
                    # status instead of an empty 200 stream.
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.close_connection = True
                    started = True
                if kind == "delta":
                    self._write_chunk(
                        {
                            **envelope,
                            "object": "chat.completion.chunk",
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {"role": "assistant", "content": value},
                                    "finish_reason": None,
                                }
                            ],
                        }
                    )
                else:
                    self._write_chunk(
                        {
                            **envelope,
                            "object": "chat.completion.chunk",
                            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                            "usage": _usage(value.get("usage")),
                        }
                    )
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
        except ProxyError:
            if not started:
                raise
            # Mid-stream failure: the status line is already sent, so the only
            # honest signal left is closing the stream without [DONE].
            self.log_message("stream failed after first token")
        except BrokenPipeError:
            self.log_message("client disconnected mid-stream")

    def _write_chunk(self, payload: dict) -> None:
        self.wfile.write(b"data: " + json.dumps(payload).encode() + b"\n\n")
        self.wfile.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("CLAUDE_PROXY_HOST", "0.0.0.0"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("CLAUDE_PROXY_PORT", "8787"))
    )
    parser.add_argument("--model", default=os.environ.get("CLAUDE_PROXY_MODEL", "opus"))
    parser.add_argument("--binary", default=os.environ.get("CLAUDE_PROXY_BIN", "claude"))
    parser.add_argument(
        "--concurrency",
        type=int,
        default=int(os.environ.get("CLAUDE_PROXY_CONCURRENCY", "4")),
        help="max simultaneous claude processes (each is a full node runtime)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.environ.get("CLAUDE_PROXY_TIMEOUT", "600")),
        help="seconds to wait for one completion",
    )
    args = parser.parse_args()

    binary = shutil.which(args.binary)
    if binary is None:
        print(f"claude binary not found: {args.binary}", file=sys.stderr)
        return 1
    args.binary = binary

    # An empty working directory on purpose: run from the repo and the CLI
    # would auto-discover ResearcherX's own CLAUDE.md and prepend it to every
    # request the app makes.
    args.cwd = tempfile.mkdtemp(prefix="claude-proxy-")

    Handler.config = Config(args)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    print(
        f"[claude-proxy] {binary} · model={args.model} · concurrency={args.concurrency} "
        f"· listening on http://{args.host}:{args.port}/v1",
        file=sys.stderr,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        shutil.rmtree(args.cwd, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
