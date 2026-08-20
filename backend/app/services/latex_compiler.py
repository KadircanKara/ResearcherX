"""Talks to the sandboxed compile service.

Every failure here degrades rather than raises: a compile that cannot run is
reported to the user as a failed compile with a generic message, and a sync
query that cannot be answered returns None so the editor keeps working without
navigation. The chat pipeline's fail-open convention, applied to a different
subsystem.
"""

import asyncio
import base64
import io
import tarfile
import tempfile
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from app.core.config import settings
from app.core.logging import log

# Bounds concurrent COMPILES only -- synctex is short and cheap and does not
# spawn latexmk, so it is deliberately left outside this gate (see the call
# site in compile_tree below). Module-level, valid only because uvicorn
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
    # None for a degraded result. Set by compile_tree from the
    # tar-compile response -- the
    # extraction directory the tree was built in, needed so a later
    # reverse-sync query can resolve tree-relative paths (see _tree_path in
    # latex-compiler/app.py).
    root: str | None = None


@dataclass(frozen=True)
class PdfPosition:
    page: int
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class SourcePoint:
    """Where a reverse SyncTeX query landed. `file` is tree-relative."""

    file: str
    line: int


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


async def _post_body(path: str, content: AsyncIterator[bytes], headers: dict) -> dict | None:
    """Like `_post`, but for a raw streaming body rather than a JSON payload.

    Kept as a twin of `_post` rather than a shared branch: the two bodies
    (`json=` vs `content=`) are different httpx call shapes, and folding
    them into one function would make the common case (`_post`, used by
    every JSON route) harder to read for a body kind it never carries.
    """
    try:
        async with httpx.AsyncClient(timeout=settings.latex_compile_timeout) as client:
            response = await client.post(
                f"{settings.latex_compiler_url}{path}", content=content, headers=headers
            )
            response.raise_for_status()
            return response.json()
    except Exception as exc:
        log.warning("latex_compiler_unavailable", path=path, error=str(exc)[:200])
        return None


def _build_tar(entries: Sequence[tuple[str, bytes]]) -> tempfile.SpooledTemporaryFile:
    """Tar the tree into a spooled file.

    Spooled rather than BytesIO: the tree is capped at 25MB and holding both
    it and its tar in memory doubles that on a single-worker process. Small
    projects -- the overwhelming majority -- never touch the disk.

    Wrapped in its own try/finally: `compile_tree`'s try/finally that closes
    the spool doesn't start until AFTER this function returns, so an
    exception raised inside `tf.addfile` (e.g. a pathological entry) would
    otherwise leak the spool from this scope -- reclaimed only by
    refcounting, not deterministically.
    """
    spool = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
    try:
        with tarfile.open(fileobj=spool, mode="w") as tf:
            for path, data in entries:
                info = tarfile.TarInfo(path)
                info.size = len(data)
                info.mode = 0o644
                tf.addfile(info, io.BytesIO(data))
    except Exception:
        spool.close()
        raise
    spool.seek(0)
    return spool


async def _aiter_spooled(
    spool: tempfile.SpooledTemporaryFile, chunk_size: int = 64 * 1024
) -> AsyncIterator[bytes]:
    """Wrap a sync `SpooledTemporaryFile` as an async byte iterator.

    Handing the spool to httpx directly (`content=spool`) makes it build a
    SyncByteStream (the file is Iterable, not AsyncIterable), which
    `httpx.AsyncClient` cannot send -- measured against the real compiler:
    "Attempted to send a sync request with an AsyncClient instance." This
    still reads one chunk at a time rather than materialising the tar.
    """
    while True:
        chunk = spool.read(chunk_size)
        if not chunk:
            break
        yield chunk


async def compile_tree(
    entries: Sequence[tuple[str, bytes]], engine: str, main_path: str
) -> CompileResult:
    """Compile a whole project tree. `main_path` is tree-relative.

    Posts `application/x-tar` with `X-Engine`/`X-Main-Path` headers, per the
    wire protocol `latex-compiler/app.py::compile_tree` implements. The tar
    is built into a spooled file and streamed to httpx chunk by chunk (see
    `_aiter_spooled`) so the process never holds the tree bytes and the tar
    bytes in memory at once for anything past the spool's 8MB threshold.
    """
    spool = _build_tar(entries)
    try:
        # httpx cannot peek an async iterator's length, so without this it
        # sends `Transfer-Encoding: chunked` -- which the compiler's stdlib
        # `BaseHTTPRequestHandler` does not decode (it reads exactly
        # `Content-Length` bytes and nothing else), so a chunked body arrives
        # as a truncated, unparsable tar. Measured against the real
        # container: "The uploaded project could not be unpacked." An
        # explicit Content-Length here makes httpx skip the auto
        # Transfer-Encoding header entirely (see httpx.Request._prepare).
        #
        # LATENT CONSTRAINT ON THE SERVICE, not just on this client: the
        # compiler never rejects a chunked body or errors on it -- it
        # misparses the chunk-size line as tar bytes and silently truncates
        # the archive, with no error surfaced anywhere. Any future caller of
        # POST /compile (or /synctex) MUST send an explicit Content-Length;
        # falling back to chunked encoding corrupts the upload silently.
        spool.seek(0, io.SEEK_END)
        size = spool.tell()
        spool.seek(0)
        headers = {
            "Content-Type": "application/x-tar",
            "Content-Length": str(size),
            "X-Engine": engine,
            # Percent-encoded: httpx encodes header values as ASCII, and a
            # non-ASCII main file name (plan 2 supports these -- its own
            # round-trip tests cover resume/cafe.tex) would otherwise raise
            # UnicodeEncodeError inside the request build, which _post_body's
            # except turns into a permanent-looking "compiler unavailable"
            # for what is actually an ordinary filename. The tar itself
            # carries non-ASCII paths natively (PAX); only this header
            # needs the round trip. The compiler unquotes it back.
            "X-Main-Path": quote(main_path, safe="/"),
        }
        # Same gate as compile_tree, for the same reason: a compile is
        # bounded CPU work in the sandboxed container, whole-tree or not.
        async with _compile_semaphore:
            payload = await _post_body("/compile", _aiter_spooled(spool), headers)
    finally:
        spool.close()
    if payload is None:
        return CompileResult(ok=False, log=_UNAVAILABLE, pdf=None, synctex_gz=None, root=None)
    try:
        pdf_b64 = payload.get("pdf_b64")
        synctex_b64 = payload.get("synctex_b64")
        # Same treatment as `file` in synctex_reverse below: `root` is
        # untyped JSON from a separately deployed image. An unguarded
        # non-string value would flow straight into CachedBuild.root and
        # back out onto a later /synctex payload.
        raw_root = payload.get("root")
        root = raw_root if isinstance(raw_root, str) else None
        return CompileResult(
            ok=bool(payload.get("ok")),
            log=payload.get("log") or "",
            pdf=base64.b64decode(pdf_b64) if pdf_b64 else None,
            synctex_gz=base64.b64decode(synctex_b64) if synctex_b64 else None,
            root=root,
        )
    except Exception as exc:
        # Same reasoning as compile_tree's matching except: shaping the
        # RESPONSE can fail independently of the request succeeding.
        log.warning("latex_compiler_bad_payload", path="/compile", error=str(exc)[:200])
        return CompileResult(ok=False, log=_UNAVAILABLE, pdf=None, synctex_gz=None, root=None)


def _sync_artifacts(pdf: bytes, synctex_gz: bytes) -> dict:
    # NO "source" key. Measured in plan 1: both sync directions answer from
    # the PDF and the map alone, with no .tex staged in the compiler at all.
    # Sending it would ship up to 25MB of project text to answer one
    # double-click.
    return {
        "pdf_b64": base64.b64encode(pdf).decode(),
        "synctex_b64": base64.b64encode(synctex_gz).decode(),
    }


async def synctex_forward(
    pdf: bytes,
    synctex_gz: bytes,
    root: str | None,
    main_path: str,
    file: str,
    line: int,
) -> PdfPosition | None:
    payload = await _post(
        "/synctex",
        {
            **_sync_artifacts(pdf, synctex_gz),
            "direction": "forward",
            "root": root,
            "main_path": main_path,
            "file": file,
            "line": line,
        },
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
    pdf: bytes,
    synctex_gz: bytes,
    root: str | None,
    main_path: str,
    page: int,
    x: float,
    y: float,
) -> SourcePoint | None:
    payload = await _post(
        "/synctex",
        {
            **_sync_artifacts(pdf, synctex_gz),
            "direction": "reverse",
            "root": root,
            "main_path": main_path,
            "page": page,
            "x": x,
            "y": y,
        },
    )
    if not payload or not payload.get("found"):
        return None
    # Validate `file` before constructing, rather than coercing with
    # `str(...)`: `str(None) == "None"` and `str(123) == "123"` are
    # confidently WRONG paths, not missing ones -- Task 4 hands `point.file`
    # straight to the editor to open, and a wrong file opens the wrong
    # document with full confidence where `found: false` renders cleanly.
    # This is exactly what the compiler's own `_tree_path` docstring rejects
    # for the same reason. A missing/wrong-typed `file` is the same
    # version-skew case the `except` below already covers for `line`; this
    # branch exists because `str()` would silently swallow it instead of
    # raising into that except.
    raw_file = payload.get("file")
    if not isinstance(raw_file, str) or not raw_file:
        log.warning(
            "latex_synctex_bad_payload",
            direction="reverse",
            error="file missing or not a string",
        )
        return None
    try:
        return SourcePoint(file=raw_file, line=int(payload["line"]))
    except Exception as exc:
        log.warning("latex_synctex_bad_payload", direction="reverse", error=str(exc)[:200])
        return None
