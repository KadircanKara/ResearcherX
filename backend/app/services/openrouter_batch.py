"""OpenRouter Batch API: build, submit and fetch paper-summary batches.

HTTP only (httpx), no DB. The backfill script owns every row write.

Facts this module relies on (OpenRouter docs, read 2026-10-27:
/docs/guides/features/batch-api, /docs/batch-quickstart,
/docs/api/api-reference/batch/create-a-batch and /get-a-batch):

- `POST /api/v1/batches` takes `endpoint`, `model`, `provider` (only `only`
  and `allow_fallbacks` are honoured) and an inline `requests` array of
  `{custom_id, body}`. It answers 202 with the batch in `validating`; a 202
  only means stored, and a request that fails validation later moves the
  whole batch to `failed`. 402 means the estimated cost exceeds the balance.
  413 is a body over 200 MB. No cost estimate is returned.
- `GET /api/v1/batches/{id}` returns the batch. `results` stays null until
  `completed`, then holds one `{custom_id, response: {status_code, body},
  error}` per request, in no particular order and unpaginated. Cost is
  batch-level (`usage.cost`), as is `provider`. 410 once the results expire
  (30 days); 402 returns the batch with `results: null` until the balance
  covers the charge.
"""

import json
from collections.abc import Sequence
from typing import Any
from urllib.parse import quote

import httpx

from app.core.config import settings
from app.services.paper_summary_prompt import messages, response_format

ENDPOINT = "/v1/chat/completions"
RUNNING = frozenset({"validating", "in_progress", "finalizing", "cancelling"})
FINAL = frozenset({"completed", "failed", "expired", "cancelled"})
FAILED_FINAL = FINAL - {"completed"}

# The documented 413 ceiling is 200 MB per submit. A margin keeps a body that
# is slightly bigger on the wire (headers, a different serialiser) under it.
DOCUMENTED_MAX_BYTES = 200_000_000
MAX_BODY_BYTES = int(DOCUMENTED_MAX_BYTES * 0.9)

# Uploading a large body takes a while; reading a batch can be large too.
_TIMEOUT = httpx.Timeout(connect=15.0, read=180.0, write=600.0, pool=15.0)
_MESSAGE_MAX = 500

Paper = tuple[str, str, str | None, str]  # (paper_id, title, abstract, text)


# Submit answers that PROVE no batch was created: the request was refused
# as written (bad input, auth, balance, policy, size, constraint). Anything
# else after a POST (5xx, 408, 429, a gateway timeout, a 202 we cannot read)
# may have stored a batch, so it is never treated as definite.
DEFINITE_REFUSALS = frozenset({400, 401, 402, 403, 404, 413, 422})


class BatchError(Exception):
    """A non-success answer from the Batch API. Never carries the key.

    `definite` is True only when the answer proves no batch exists (a
    `DEFINITE_REFUSALS` status, or a refusal before any request was sent).
    It defaults to False so a new caller can never release work by accident.
    """

    def __init__(self, status: int, message: str, *, definite: bool = False) -> None:
        super().__init__(f"OpenRouter batch error {status}: {message}")
        self.status = status
        self.message = message
        self.definite = definite


def _request(paper: Paper) -> dict:
    paper_id, title, abstract, text = paper
    return {
        "custom_id": paper_id,
        "body": {
            "max_tokens": settings.paper_summary_max_tokens,
            "reasoning": {"enabled": False},
            "response_format": response_format(),
            "messages": messages(title, abstract, text),
        },
    }


def _envelope(model: str, provider: str) -> dict:
    # Key order is part of the contract: `endpoint, model, provider` BEFORE
    # `requests`, so the routing fields are read before a large array.
    return {
        "endpoint": ENDPOINT,
        "model": model,
        "provider": {"only": [provider], "allow_fallbacks": False},
    }


def batch_body(papers: Sequence[Paper], *, model: str, provider: str) -> dict:
    return {**_envelope(model, provider), "requests": [_request(p) for p in papers]}


def _size(obj: Any) -> int:
    return len(json.dumps(obj).encode())


def chunk_papers(
    papers: Sequence[Paper], *, max_count: int, max_bytes: int, model: str, provider: str
) -> list[list[Paper]]:
    """Split into submits of at most `max_count` papers and `max_bytes` bytes.

    Sizes are measured on the JSON `submit` sends. Raises ValueError when one
    paper alone cannot fit: it is never sent oversized.
    """
    if max_count < 1:
        raise ValueError("max_count must be at least 1")
    # The envelope with an empty requests array, plus one comma per request
    # after the first; json.dumps' ", " separator is two bytes.
    base = _size({**_envelope(model, provider), "requests": []})
    chunks: list[list[Paper]] = []
    current: list[Paper] = []
    used = base
    for paper in papers:
        size = _size(_request(paper))
        if base + size > max_bytes:
            raise ValueError(f"paper {paper[0]} alone is {size} bytes, over {max_bytes}")
        extra = size + (2 if current else 0)
        if current and (len(current) >= max_count or used + extra > max_bytes):
            chunks.append(current)
            current, used, extra = [], base, size
        current.append(paper)
        used += extra
    if current:
        chunks.append(current)
    return chunks


def _headers() -> dict[str, str]:
    if not settings.summary_api_key:
        raise BatchError(0, "SUMMARY_API_KEY is not set", definite=True)
    return {
        "Authorization": f"Bearer {settings.summary_api_key}",
        "Content-Type": "application/json",
    }


def _url(path: str) -> str:
    return f"{settings.summary_base_url.rstrip('/')}{path}"


def _message(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text[:_MESSAGE_MAX] or response.reason_phrase
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict) and isinstance(err.get("message"), str):
            return err["message"][:_MESSAGE_MAX]
        if isinstance(err, str):
            return err[:_MESSAGE_MAX]
    return json.dumps(data)[:_MESSAGE_MAX]


async def _send(method: str, path: str, client: httpx.AsyncClient | None, **kw) -> httpx.Response:
    headers = _headers()
    if client is not None:
        return await client.request(method, _url(path), headers=headers, timeout=_TIMEOUT, **kw)
    async with httpx.AsyncClient(timeout=_TIMEOUT) as own:
        return await own.request(method, _url(path), headers=headers, **kw)


async def submit(body: dict, *, client: httpx.AsyncClient | None = None) -> str:
    """POST one batch; its id. BatchError on anything but a readable 202;
    `definite` only for a `DEFINITE_REFUSALS` status (402 included)."""
    # Serialised here, not via `json=`, so the bytes sent are the bytes
    # `chunk_papers` measured, in the key order `batch_body` built.
    response = await _send("POST", "/batches", client, content=json.dumps(body).encode())
    status = response.status_code
    if status != 202:
        raise BatchError(status, _message(response), definite=status in DEFINITE_REFUSALS)
    try:
        data = response.json()
    except ValueError:
        data = None
    batch_id = data.get("id") if isinstance(data, dict) else None
    if not isinstance(batch_id, str) or not batch_id:
        # Stored, probably; we just cannot tell under which id.
        raise BatchError(status, "202 without a readable batch id", definite=False)
    return batch_id


async def list_batches(
    *, limit: int = 20, after: str | None = None, client: httpx.AsyncClient | None = None
) -> dict:
    """`GET /batches`: newest first, `limit` 1..100, cursor `after`; list
    items carry `results: null`. How an orphaned submit is found."""
    params: dict[str, Any] = {"limit": max(1, min(limit, 100))}
    if after:
        params["after"] = after
    response = await _send("GET", "/batches", client, params=params)
    if response.status_code != 200:
        raise BatchError(response.status_code, _message(response))
    try:
        data = response.json()
    except ValueError as exc:
        raise BatchError(response.status_code, "list answer is not JSON") from exc
    if not isinstance(data, dict):
        raise BatchError(response.status_code, "list answer is not an object")
    return data


def listed(data: dict) -> list[dict]:
    """The batch objects of a `list_batches` answer."""
    items = data.get("data")
    return [b for b in items if isinstance(b, dict)] if isinstance(items, list) else []


async def fetch(batch_id: str, *, client: httpx.AsyncClient | None = None) -> dict:
    """GET one batch. 402 still returns the batch (results withheld until
    the balance covers it); any other non-200 raises BatchError."""
    response = await _send("GET", f"/batches/{quote(batch_id, safe='')}", client)
    if response.status_code in (200, 402):
        try:
            data = response.json()
        except ValueError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("status"), str):
            return data
    raise BatchError(response.status_code, _message(response))
