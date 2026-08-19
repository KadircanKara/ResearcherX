"""Talks to the sandboxed compile service.

Every failure here degrades rather than raises: a compile that cannot run is
reported to the user as a failed compile with a generic message, and a sync
query that cannot be answered returns None so the editor keeps working without
navigation. The chat pipeline's fail-open convention, applied to a different
subsystem.
"""

import asyncio
import base64
from dataclasses import dataclass

import httpx

from app.core.config import settings
from app.core.logging import log

# Bounds concurrent COMPILES only -- synctex is short and cheap and does not
# spawn latexmk, so it is deliberately left outside this gate (see the call
# site in compile_source below). Module-level, valid only because uvicorn
# runs a single worker -- the same invariant the event bus, the rate limiter
# and latex_cache already depend on. See `latex_max_concurrent_compiles` in
# config.py for the pids_limit math behind the number.
#
# An asyncio.Semaphore binds itself to the event loop that first CONTENDS it,
# so a module-level one is only safe because the app has exactly one loop.
# Under pytest, where every test gets a fresh loop, contending this object from
# a second loop raises "RuntimeError: bound to a different event loop" -- which
# is why the concurrency tests patch in a freshly constructed semaphore rather
# than reusing this one. That patching is load-bearing, not incidental style.
_compile_semaphore = asyncio.Semaphore(settings.latex_max_concurrent_compiles)


@dataclass(frozen=True)
class CompileResult:
    ok: bool
    log: str
    pdf: bytes | None
    synctex_gz: bytes | None


@dataclass(frozen=True)
class PdfPosition:
    page: int
    x: float
    y: float
    width: float
    height: float


_UNAVAILABLE = "The LaTeX compiler is unavailable. Please try again."


async def _post(path: str, payload: dict) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=settings.latex_compile_timeout) as client:
            response = await client.post(f"{settings.latex_compiler_url}{path}", json=payload)
            response.raise_for_status()
            return response.json()
    except Exception as exc:
        # str(exc) stays in the server log and never reaches the client.
        log.warning("latex_compiler_unavailable", path=path, error=str(exc)[:200])
        return None


async def compile_source(source: str, engine: str) -> CompileResult:
    # Queue rather than reject beyond the limit: a compile is normally
    # sub-second, and the failure this guards against -- pid exhaustion in
    # the compiler container -- is neither self-limiting nor confined to
    # whoever caused it, so a slower response beats a 429 that some other
    # request's flood earned this one.
    async with _compile_semaphore:
        payload = await _post("/compile", {"source": source, "engine": engine})
    if payload is None:
        return CompileResult(ok=False, log=_UNAVAILABLE, pdf=None, synctex_gz=None)
    try:
        pdf_b64 = payload.get("pdf_b64")
        synctex_b64 = payload.get("synctex_b64")
        return CompileResult(
            ok=bool(payload.get("ok")),
            log=payload.get("log") or "",
            pdf=base64.b64decode(pdf_b64) if pdf_b64 else None,
            synctex_gz=base64.b64decode(synctex_b64) if synctex_b64 else None,
        )
    except Exception as exc:
        # Shaping the RESPONSE can fail too, not just the network call: a
        # truncated or malformed base64 body raises binascii.Error. Guarding
        # only _post would leave the module's fail-open promise true of the
        # request and false of the reply.
        log.warning("latex_compiler_bad_payload", path="/compile", error=str(exc)[:200])
        return CompileResult(ok=False, log=_UNAVAILABLE, pdf=None, synctex_gz=None)


def _artifacts(source: str, pdf: bytes, synctex_gz: bytes) -> dict:
    return {
        "source": source,
        "pdf_b64": base64.b64encode(pdf).decode(),
        "synctex_b64": base64.b64encode(synctex_gz).decode(),
    }


async def synctex_forward(
    source: str, pdf: bytes, synctex_gz: bytes, line: int
) -> PdfPosition | None:
    payload = await _post(
        "/synctex", {**_artifacts(source, pdf, synctex_gz), "direction": "forward", "line": line}
    )
    if not payload or not payload.get("found"):
        return None
    try:
        return PdfPosition(
            page=int(payload["page"]),
            x=float(payload["x"]),
            y=float(payload["y"]),
            width=float(payload.get("width") or 0),
            height=float(payload.get("height") or 0),
        )
    except Exception as exc:
        # `found: true` is not a promise that the other keys are present. The
        # client and the compile service are separately deployed images, so a
        # version skew must degrade to "no navigation", never raise into a
        # chat turn.
        log.warning("latex_synctex_bad_payload", direction="forward", error=str(exc)[:200])
        return None


async def synctex_reverse(
    source: str, pdf: bytes, synctex_gz: bytes, page: int, x: float, y: float
) -> int | None:
    payload = await _post(
        "/synctex",
        {
            **_artifacts(source, pdf, synctex_gz),
            "direction": "reverse",
            "page": page,
            "x": x,
            "y": y,
        },
    )
    if not payload or not payload.get("found"):
        return None
    try:
        return int(payload["line"])
    except Exception as exc:
        log.warning("latex_synctex_bad_payload", direction="reverse", error=str(exc)[:200])
        return None
