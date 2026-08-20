"""LaTeX compilation, isolated from everything worth stealing.

This service exists because compiling user-authored LaTeX is arbitrary code
execution and the engine flags do not close it:

- `-no-shell-escape` blocks `\\write18`, but not LuaTeX's `\\directlua`.
  LuaTeX's `--safer` would, and it aborts every real compile of a normal
  preamble (`luaotfload.lua:105: error("safer_option used")`) -- so the flag
  that would help is unusable. (This service does not offer lualatex as an
  engine -- see `_ENGINE_FLAG` below for why -- but the point generalizes:
  no engine's own flags are a trustworthy boundary, which is why the
  containment below has to be the container, not the flags.)
- File READS survive every engine flag. `\\input{/app/.env}` pulls secrets
  straight into the compiled PDF.
- A ten-line macro exhausts RAM or spins forever, usually by accident.

So the containment is the container, not the flags: no secrets in this
process's environment, no route to the database, no egress, read-only rootfs,
tmpfs workdir, non-root, dropped capabilities, and hard resource limits. The
flags below are defence in depth on top of that, not the control.

STATELESS on purpose. Artifacts are returned to the caller and nothing is kept
between requests, so a restart loses nothing and there is no cache here to
poison.
"""

import base64
import json
import os
import posixpath
import signal
import subprocess
import tarfile
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

# Wall-clock ceiling for one compile. A runaway document is the common case
# (a recursive macro), not the rare one.
COMPILE_TIMEOUT = 30
SYNCTEX_TIMEOUT = 10

# LaTeX source is text; a request claiming more than this is a mistake or an
# attempt to make us buffer a huge body before compilation even starts.
#
# /synctex carries the PDF (and the SyncTeX map) base64-encoded, which
# inflates the wire size 4/3 over the raw bytes. 5MB used to cap SyncTeX
# navigation for any PDF over ~3.7MB -- measured live: a 3.5MB PDF (4.67MB
# body) worked, a 4MB PDF (5.33MB body) was rejected. 16MB gives a ~10MB PDF
# room for its base64 body (~13.3MB) plus the synctex map and source text
# alongside it.
MAX_CONTENT_LENGTH = 16 * 1024 * 1024

# A tar of a 25MB tree plus headers. The JSON limit above still governs
# /synctex, which carries base64 and is bounded by the PDF, not the tree.
MAX_TAR_LENGTH = 32 * 1024 * 1024

# Drain at most this much of an over-declared/over-long body. The bound
# exists to cap WORK, not to distinguish good clients from bad ones -- a
# byte bound cannot tell the two things it's balancing apart:
#
# - Bytes the client has ALREADY WRITTEN into the socket (real trailing
#   data under an honest Content-Length, or the tail of a lying one).
#   Draining these is fast and is the entire point: leaving them unread is
#   what makes the OS answer a close with an RST instead of a clean FIN,
#   which swallows the response the client is waiting for.
# - Bytes the client will NEVER send (a Content-Length that over-declares
#   and then goes silent). Waiting for these is the stall the guarded
#   try/except below exists to survive without losing the response.
#
# A smaller bound (1MB, this file's first attempt) narrowed the FIRST
# failure mode instead of eliminating it: a valid tar plus 5MB of REAL,
# already-sent trailing bytes under an HONEST Content-Length reproduced the
# exact RST-swallows-the-response bug at that larger size (3/3 runs,
# ConnectionResetError/BrokenPipeError, no response delivered) -- the same
# defect rounds 2 through 4 kept finding, just past whatever bound was
# tried. Raising the bound to MAX_TAR_LENGTH costs nothing new because both
# failure modes are already bounded independently of this constant: no
# legitimate OR hostile request can ever contain more real trailing bytes
# than MAX_TAR_LENGTH (anything larger was already refused with a 413
# before either drain call runs), so a bound at the wire cap drains
# everything a request can actually carry and the RST class disappears
# entirely within it. A client that stops sending is bounded by
# `Handler.timeout` and the surrounding guard, not by this number -- that
# path was already "bounded, not prompt" before this change and stays that
# way; verified live, a 30MB over-declaration with the socket held open
# blocks roughly 30s and then still delivers a correct `200 ok:true`.
MAX_DRAIN_BYTES = MAX_TAR_LENGTH

# latexmk engine flag per document engine. latexmk rather than a bare engine
# call because a paper has citations and cross-references: without its rerun
# and bibtex passes every reference renders as [?] and every \ref as ??.
#
# lualatex is unsupported in v1. luaotfload (LuaTeX's font loader) demands a
# writable cache path during format load, before any document content runs --
# confirmed live: every TeX Live writable-path env var (TEXMFVAR, TEXMFCACHE,
# TEXMFHOME, TEXMFCONFIG, TEXMFSYSVAR, TEXMFSYSCONFIG) pointed at the tmpfs,
# and lualatex still fails identically ("no writeable cache path, quiting")
# even when its own format file is freshly regenerated onto that same tmpfs.
# This is a read-only-rootfs interaction with luaotfload's own cache-path
# resolution, not a document problem, so no per-document workaround exists.
# An unknown engine already falls back to pdflatex rather than erroring, so
# dropping this entry degrades safely.
_ENGINE_FLAG = {
    "pdflatex": "-pdf",
    "xelatex": "-pdfxe",
}


def _first_error(log_text: str) -> str:
    """The first real TeX error, or the tail of the log.

    A TeX log is thousands of lines of font loading; the line starting with
    "!" is the part a human needs. Returning the tail rather than nothing
    when there is no "!" keeps a timeout or a driver failure diagnosable.
    """
    for i, line in enumerate(log_text.splitlines()):
        if line.startswith("!"):
            return "\n".join(log_text.splitlines()[i : i + 12])
    return "\n".join(log_text.splitlines()[-40:])


class _Bounded:
    """Reads at most `limit` bytes from `stream`. tarfile in stream mode will
    happily read as much as it is given, and nothing else stops it reading
    past the declared body.

    That does not smuggle bytes into a NEXT request today: `Handler` never
    sets `protocol_version`, so every response here is HTTP/1.0 and each
    connection serves exactly one request -- verified live, a pipelined
    `GET /health` sent after a short-`Content-Length` tar body gets exactly
    one response, not two. This class is defence in depth against that
    assumption changing (keep-alive, a future `protocol_version` bump), not
    a fix for a smuggling window that exists today.

    `remaining` exposes how much of `limit` is still unread so a caller can
    drain it before answering -- see the 413 branch's comment on why an
    unread remainder in the kernel buffer can turn a clean response into a
    TCP RST on the client.
    """

    def __init__(self, stream, limit: int) -> None:
        self._stream = stream
        self._left = limit

    @property
    def remaining(self) -> int:
        return self._left

    def read(self, size: int = -1) -> bytes:
        if self._left <= 0:
            return b""
        want = self._left if size is None or size < 0 else min(size, self._left)
        chunk = self._stream.read(want)
        self._left -= len(chunk)
        return chunk


def _strict_filter(member: tarfile.TarInfo, path: str) -> tarfile.TarInfo:
    """Refuse what `data_filter` would merely contain.

    CPython's `data_filter` strips a leading slash and rewrites `/etc/passwd`
    to `<dest>/etc/passwd` -- contained, but accepted. Nothing this service is
    sent should ever hold an absolute or parent-relative name: `latex_archive`
    rejects both in the backend process before a tar is ever built. Refusing
    here keeps the two guards saying the same thing, so a tar that violates
    the first is not quietly normalised by the second.
    """
    name = member.name
    if name.startswith("/") or name.startswith("\\"):
        raise ValueError(f"absolute path in archive: {name}")
    if ".." in PurePosixPath(name).parts:
        raise ValueError(f"parent-directory segment in archive: {name}")
    return tarfile.data_filter(member, path)


# The backend caps an uploaded tree at 25MB and 2000 files (latex_archive).
# 4000 members and 64MB extracted leaves ample headroom for anything
# legitimate while turning a pathological tar into one clean rejection
# instead of exhausting the container's mem_limit for everyone. The member
# count is the load-bearing half of this pair: measured in-container, a
# 31.3MB tar of 32,000 one-byte files extracted to 125MB of tmpfs -- each
# file costs a 4096-byte page regardless of its declared size, so summing
# declared sizes alone would not have caught it. Tar itself is uncompressed,
# so there is no separate decompression-bomb case to guard against here.
#
# MAX_EXTRACTED_BYTES is UNREACHABLE over HTTP today, and that is fine: the
# 32MB wire cap (MAX_TAR_LENGTH) binds first for an uncompressed tar, so no
# request can ever declare a byte total this filter would refuse before the
# wire cap already rejected it with a 413. It stays as a second, independent
# ceiling in case that relationship ever changes (a compressed transport, a
# raised wire cap) -- this is not dead code to be pruned, it is a guard for
# an invariant (uncompressed tar, 32MB cap) that lives in a different part
# of this file and could drift out of sync with this one.
MAX_TAR_MEMBERS = 4000
MAX_EXTRACTED_BYTES = 64 * 1024 * 1024


def _extract_tree(bounded: "_Bounded", directory: Path) -> None:
    """Extract the posted tar into `directory`.

    `_strict_filter` runs our own absolute/`..` refusal first, then delegates
    to `filter="data"` -- CPython's own guard against symlinks and hardlinks
    pointing outside, and device nodes. `data_filter` alone is the SECOND
    independent traversal guard -- `latex_archive` already validated every
    path in the backend process -- and it is deliberately not hand-rolled
    here, in the one container where being wrong about it is worst; the
    strict layer on top only tightens what it accepts, never replaces it.
    A running member count and declared-byte total wrap that filter so a
    pathological tar (too many entries, or a declared size too large) is
    refused mid-stream rather than fully extracted first.

    Streamed with mode "r|" so the archive is never materialised: a 32MB tar
    read into memory before extraction would double the tmpfs cost of every
    compile, and /tmp is RAM charged against this container's mem_limit.
    `bounded` is passed in already constructed so the caller can inspect
    `.remaining` afterwards and drain any unconsumed request body.
    """
    member_count = 0
    extracted_bytes = 0

    def _filter(member: tarfile.TarInfo, path: str) -> tarfile.TarInfo:
        nonlocal member_count, extracted_bytes
        member_count += 1
        if member_count > MAX_TAR_MEMBERS:
            raise ValueError(f"archive has more than {MAX_TAR_MEMBERS} entries")
        extracted_bytes += max(member.size, 0)
        if extracted_bytes > MAX_EXTRACTED_BYTES:
            raise ValueError(
                f"archive extracts to more than {MAX_EXTRACTED_BYTES} bytes"
            )
        return _strict_filter(member, path)

    with tarfile.open(fileobj=bounded, mode="r|") as tf:
        tf.extractall(directory, filter=_filter)


def compile_tree(bounded: "_Bounded", engine: str, main_path: str) -> dict:
    """Compile a whole tree. `main_path` is tree-relative.

    `bounded` is a `_Bounded` wrapper the caller already constructed around
    the request body, so its `.remaining` is meaningful to the caller after
    this returns (or raises) -- see the `finally: self._drain(...)` in
    `do_POST` for why the caller needs that.

    latexmk runs with `-cd`, which chdirs into the main file's directory --
    WITHOUT it, a `\\input{chapters/intro}` in `src/paper.tex` resolves
    against the tree root and fails (`rc=12`, measured). The artifacts then
    land BESIDE the main file, not at the root, which is why the PDF is read
    from `main.parent`.
    """
    flag = _ENGINE_FLAG.get(engine, "-pdf")
    with tempfile.TemporaryDirectory(prefix="rx-latex-") as tmp:
        directory = Path(tmp)
        try:
            _extract_tree(bounded, directory)
        except Exception:
            return {
                "ok": False,
                "log": "The uploaded project could not be unpacked.",
                "pdf_b64": None,
                "synctex_b64": None,
                "root": None,
            }

        main = (directory / main_path).resolve()
        # Belt and braces with filter="data": the main path is a separate
        # input from the tar's member names and gets its own containment
        # check. `resolve()` collapses any `..` before the comparison.
        if (
            not str(main).startswith(str(directory.resolve()) + os.sep)
            or not main.is_file()
        ):
            return {
                "ok": False,
                "log": "The document's main file is not in the project.",
                "pdf_b64": None,
                "synctex_b64": None,
                "root": None,
            }

        proc = subprocess.Popen(
            [
                "latexmk",
                flag,
                "-cd",
                "-synctex=1",
                "-interaction=nonstopmode",
                "-no-shell-escape",
                "-halt-on-error",
                str(main),
            ],
            cwd=directory,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=COMPILE_TIMEOUT)
            log_text = stdout + stderr
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.communicate()
            return {
                "ok": False,
                "log": f"Compilation exceeded {COMPILE_TIMEOUT}s and was stopped.",
                "pdf_b64": None,
                "synctex_b64": None,
                "root": None,
            }

        stem = main.stem
        log_file = main.parent / f"{stem}.log"
        if log_file.exists():
            log_text = log_file.read_text(encoding="utf-8", errors="replace")

        pdf = main.parent / f"{stem}.pdf"
        synctex = main.parent / f"{stem}.synctex.gz"
        if not pdf.exists():
            return {
                "ok": False,
                "log": _first_error(log_text),
                "pdf_b64": None,
                "synctex_b64": None,
                "root": None,
            }
        return {
            "ok": True,
            "log": _first_error(log_text) if proc.returncode else "",
            "pdf_b64": base64.b64encode(pdf.read_bytes()).decode(),
            "synctex_b64": (
                base64.b64encode(synctex.read_bytes()).decode()
                if synctex.exists()
                else None
            ),
            "root": str(directory),
        }


def _records(output: str) -> list[dict]:
    """Parse the synctex client's Field:value blocks into records."""
    records: list[dict] = []
    current: dict = {}
    for line in output.splitlines():
        if line.startswith("SyncTeX result begin"):
            current = {}
            continue
        if line.startswith("SyncTeX result end"):
            if current:
                records.append(current)
            current = {}
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            current[key.strip()] = value.strip()
    if current:
        records.append(current)
    return records


def query_synctex(body: dict) -> dict:
    """`body`: direction, pdf_b64, synctex_b64, main_path, root, plus the
    coordinates for that direction.

    NO SOURCE. Measured in plan 1: both directions answer from the PDF and the
    map alone, with no .tex staged at all. Shipping the tree here would mean
    sending up to 25MB to answer one double-click.

    The wire protocol is TREE-RELATIVE. synctex itself speaks paths relative
    to the main file's DIRECTORY (measured: `-i 2:0:chapters/intro.tex`
    resolves, `-i 2:0:src/chapters/intro.tex` does not), so this function owns
    both conversions. When the main file is at the tree root -- the common
    case -- every conversion is the identity.

    LIMITATION, not fixed here: forward sync cannot address a file outside
    the main file's directory. `posixpath.relpath` on such a file yields a
    `../`-prefixed path, and synctex's `view` returns NOT FOUND for it
    (measured) -- so a subdirectory main file can forward-sync anything
    under that subdirectory, but not a file reached only via `..`. Reverse
    sync has no such restriction (see `_tree_path`), so the two directions
    are asymmetric.
    """
    direction = body.get("direction")
    if direction not in ("forward", "reverse"):
        return {"found": False}

    # Type-confusion guard: these three arrive as untyped JSON. A non-string
    # value (a list, a number, null) must be treated as absent rather than
    # reaching string operations below and raising a 500 -- pdf_b64/
    # synctex_b64/the coordinates already fail correctly (base64/int/float
    # parsing raises, caught by the try/except 500 in do_POST) so only the
    # path-shaped fields need this.
    raw_main_path = body.get("main_path")
    main_path = raw_main_path if isinstance(raw_main_path, str) else ""
    raw_root = body.get("root")
    root = raw_root if isinstance(raw_root, str) else None
    raw_file = body.get("file")
    file_param = raw_file if isinstance(raw_file, str) else None

    main_dir = posixpath.dirname(main_path)
    stem = posixpath.splitext(posixpath.basename(main_path))[0] or "master"

    with tempfile.TemporaryDirectory(prefix="rx-synctex-") as tmp:
        directory = Path(tmp)
        pdf = directory / f"{stem}.pdf"
        pdf.write_bytes(base64.b64decode(body["pdf_b64"]))
        (directory / f"{stem}.synctex.gz").write_bytes(
            base64.b64decode(body["synctex_b64"])
        )

        # `-d <dir>` is load-bearing: the map records ABSOLUTE paths from the
        # directory it was compiled in, which no longer exists by the time
        # anyone queries it. Verified in jobfit against a real document --
        # both view and edit answer correctly from a directory the document
        # was never compiled in.
        if direction == "forward":
            tree_file = file_param or main_path
            # tree-relative -> main-dir-relative
            rel = posixpath.relpath(tree_file, main_dir) if main_dir else tree_file
            args = [
                "synctex",
                "view",
                "-i",
                f"{body['line']}:0:{rel}",
                "-o",
                str(pdf),
                "-d",
                str(directory),
            ]
        else:
            args = [
                "synctex",
                "edit",
                "-o",
                f"{body['page']}:{body['x']}:{body['y']}:{pdf}",
                "-d",
                str(directory),
            ]
        # Same process-group treatment as compile_tree's subprocess call, for
        # consistency: synctex is much less likely to fork a runaway child,
        # but the next person reading this file should not have to work out
        # why one of the two subprocess calls here is guarded against
        # leaving an orphan behind and the other is not.
        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, _stderr = proc.communicate(timeout=SYNCTEX_TIMEOUT)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.communicate()  # reap, do not leave a zombie
            return {"found": False}

        records = _records(stdout)
        if direction == "forward":
            boxes = [r for r in records if "Page" in r and "x" in r and "y" in r]
            if not boxes:
                return {"found": False}
            box = boxes[0]
            return {
                "found": True,
                "page": int(box["Page"]),
                "x": float(box["x"]),
                "y": float(box["y"]),
                "width": float(box.get("W") or 0),
                "height": float(box.get("H") or 0),
            }
        lines = [r for r in records if "Line" in r]
        if not lines:
            return {"found": False}
        record = lines[0]
        resolved = _tree_path(record.get("Input", ""), root)
        if resolved is None:
            return {"found": False}
        return {"found": True, "file": resolved, "line": int(record["Line"])}


def _tree_path(raw_input: str, root: str | None) -> str | None:
    """Turn synctex's `Input:` into a TREE-RELATIVE path, or None.

    `Input:` is `<root>/<whatever the engine recorded>`. Once the recorded
    root is stripped, the remainder is already tree-relative -- an earlier
    version rejoined the main file's directory on top of it, which
    double-counted the prefix and silently returned the wrong file for any
    project whose main file sits in a subdirectory.

    Returns None rather than guessing. A wrong file opens the wrong document
    with full confidence; `found: false` renders cleanly. Refused: anything
    not under the compile root (system inputs like `article.cls` live in the
    TeX Live tree), and anything that still escapes after normalisation
    (`\\input{../../etc/hostname}` is a document the user can write).
    """
    if not raw_input or not root:
        return None
    # A bare `startswith(root)` would also match a SIBLING directory whose
    # name merely extends root's characters (`/tmp/RX/...` starts with
    # `/tmp/R`) -- the same class of bug `compile_tree`'s containment check
    # guards against with `str(directory.resolve()) + os.sep`. Requiring the
    # separator keeps both checks in this module consistent.
    prefix = root.rstrip("/") + "/"
    if not raw_input.startswith(prefix):
        return None
    tail = raw_input[len(prefix) :]
    # `/./` is how synctex separates the recorded cwd from the relative path.
    # Collapse it IN PLACE; do not treat it as a split point.
    tail = tail.replace("/./", "/")
    if tail.startswith("./"):
        tail = tail[2:]
    if not tail:
        return None
    candidate = posixpath.normpath(tail)
    if candidate.startswith("/") or candidate == ".." or candidate.startswith("../"):
        return None
    return candidate


class Handler(BaseHTTPRequestHandler):
    """Two POST routes and a health check, on the standard library.

    ThreadingHTTPServer, not the single-threaded default: two members
    compiling at once would otherwise serialise behind a 30-second run, and
    the container's `pids_limit` is what bounds concurrency instead.
    """

    server_version = "latex-compiler"

    # BaseHTTPRequestHandler sets no socket timeout, so a client that claims a
    # large Content-Length and then stalls pins its worker thread for as long
    # as it likes. With ThreadingHTTPServer that is a thread leak: enough
    # stalled connections and no member can compile at all. The timeout is per
    # socket operation, not per request, so it never interrupts a compile --
    # nothing reads or writes this socket during the 30s COMPILE_TIMEOUT -- and
    # a client that keeps making progress is never cut off. On expiry the
    # handler closes the connection and the thread is returned.
    timeout = 30

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _drain(self, length: int) -> None:
        """Read and discard up to `length` bytes of the request body without
        ever materialising it all at once. See the 413 branch in do_POST for
        why this has to happen before that response is sent. Callers are
        responsible for bounding `length` by MAX_DRAIN_BYTES themselves --
        the two call sites bound different things (a hostile DECLARED
        length vs. a tar's genuinely unread remainder), so the bound is kept
        explicit at each call site rather than hidden as a default here.
        Neither call site guards this with try/except internally either --
        that is each caller's job too, since draining is a courtesy that
        must never cost an already-decided response (see both call sites)."""
        remaining = length
        chunk_size = 64 * 1024
        while remaining > 0:
            chunk = self.rfile.read(min(chunk_size, remaining))
            if not chunk:
                break
            remaining -= len(chunk)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(200, {"ok": True})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        # All three malformed-header cases are guarded BEFORE the body is
        # ever read: an unparseable header would otherwise reach rfile.read()
        # as an uncaught exception (a raw connection drop instead of the
        # clean JSON every other path returns, plus a full traceback dumped
        # to container logs by socketserver's default handler -- routing
        # around both the internals-free 500 below and log_message's
        # override). A negative length is parseable but must never reach
        # rfile.read(): read(-1) on a live socket blocks until the peer
        # closes, hanging the thread. A too-large length is rejected before
        # buffering that many bytes at all.
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send(400, {"error": "invalid content-length"})
            return
        if length < 0:
            self._send(400, {"error": "invalid content-length"})
            return
        # /compile speaks only the tar transport now, so it always gets the
        # larger cap (MAX_TAR_LENGTH, 32MB) regardless of what Content-Type
        # the client claims -- a mislabelled or missing header must not let
        # a 20MB tar sneak in under the smaller limit. MAX_CONTENT_LENGTH
        # (16MB) governs everything else, which today is only /synctex's
        # base64 JSON body.
        is_tar = (
            self.path == "/compile"
            and self.headers.get("Content-Type") == "application/x-tar"
        )
        limit = MAX_TAR_LENGTH if self.path == "/compile" else MAX_CONTENT_LENGTH
        if length > limit:
            # Drain the body before answering. By the time we know it is too
            # large, the client has typically already written most or all of
            # it into the socket. Responding and closing without reading
            # those bytes leaves them unread in the kernel's receive buffer,
            # and the OS answers a close-with-unread-data by sending a TCP
            # RST instead of a clean FIN. That RST can land mid-write or
            # mid-read on the client, so instead of a readable 413 it sees a
            # bare connection error -- reproduced live as
            # `httpx.ReadError('')`, logged as `latex_compiler_unavailable
            # error=` with an empty error string: an undelivered 413 is
            # indistinguishable from the service being down. Read in bounded
            # chunks rather than one `rfile.read(length)` call so a hostile
            # Content-Length cannot force a single huge allocation. Bounded
            # by MAX_DRAIN_BYTES (not the full declared `length`, which can
            # be enormous for exactly the hostile client this branch exists
            # for) and guarded: this predates the tar transport, and it had
            # the same unguarded-drain defect Finding 1 found and fixed
            # below -- reproduced live, a request declaring Content-Length
            # 34MB with zero body bytes ever sent made this hang for the
            # full 30s and then drop the connection with no 413 delivered
            # at all. Draining here is the same courtesy as the tar path's:
            # never worth losing the response that is already decided.
            try:
                self._drain(min(length, MAX_DRAIN_BYTES))
            except Exception:
                pass
            self._send(413, {"error": "request too large"})
            return
        if is_tar:
            # Inside a try/except -> 500 just like the JSON dispatch below --
            # `compile_tree` can raise on attacker-controlled input it does
            # not fully validate itself (a NUL byte in X-Main-Path reaches
            # Path.resolve() as ValueError: embedded null character in
            # path). Left outside this handler, that exception used to
            # propagate out of do_POST entirely: no HTTP response is ever
            # written, the client sees a bare dropped connection (the same
            # "indistinguishable from the service being down" failure the
            # 413 branch above exists to avoid), and the traceback goes to
            # container logs via socketserver's default handler, routing
            # around log_message's override that exists to keep request
            # data out of logs.
            engine = self.headers.get("X-Engine", "pdflatex")
            # Percent-decoded: httpx (every client) encodes header values as
            # ASCII, so a non-ASCII main file name (plan 2 supports these --
            # measured, e.g. resume/cafe.tex) arrives percent-encoded. The tar
            # itself carries non-ASCII paths natively (PAX); only this header
            # needs the round trip.
            main_path = unquote(self.headers.get("X-Main-Path", ""))
            bounded = _Bounded(self.rfile, length)
            try:
                result = compile_tree(bounded, engine, main_path)
            except Exception:
                result = None
            # Drain before responding, for the same reason the 413 branch
            # does: `compile_tree` stops reading at the tar's end-of-archive
            # marker, so any declared bytes beyond that (padding, or a lying
            # Content-Length) are still sitting unread in the kernel receive
            # buffer. Responding and closing without draining them answers
            # with a TCP RST instead of a clean FIN, which can land
            # mid-write on the client and turn a valid response into a bare
            # connection error -- measured live: a tar followed by ~25MB of
            # trailing bytes under a 32MB Content-Length produced
            # BrokenPipeError on the client with no response delivered.
            #
            # But draining is a COURTESY, never a reason to lose an already-
            # computed response, so this is both guarded AND bounded by
            # MAX_DRAIN_BYTES rather than by a socket deadline -- an earlier
            # version of this fix called `self.connection.settimeout(...)`
            # before draining and never restored it, which is a stateful
            # mistake on a shared socket: that same deadline then silently
            # governed `_send`'s write of the response a few lines below,
            # truncating every multi-MB PDF sent to a client that could not
            # read faster than the timeout allowed -- measured live, a
            # 5.6MB PDF to a slow reader delivered only 556,905 of 5,619,935
            # bytes over the tar path while an identical JSON-path control
            # (which never touches the socket timeout) delivered all of it.
            # A byte bound cannot leak into unrelated code the way a
            # deadline on the connection itself can. See MAX_DRAIN_BYTES's
            # own comment for what this bound does and does not close.
            try:
                self._drain(min(bounded.remaining, MAX_DRAIN_BYTES))
            except Exception:
                pass
            if result is None:
                self._send(500, {"error": "compile service failure"})
            else:
                self._send(200, result)
            return
        if self.path == "/compile":
            # /compile no longer speaks JSON at all -- the tar transport
            # (handled above) is the only form. Drain for the same reason
            # the 413 branch does: the body is still sitting unread in the
            # kernel receive buffer, and answering without draining it risks
            # the same RST-swallows-the-response failure.
            try:
                self._drain(min(length, MAX_DRAIN_BYTES))
            except Exception:
                pass
            self._send(400, {"error": "expected application/x-tar"})
            return
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send(400, {"error": "invalid json"})
            return
        if not isinstance(body, dict):
            self._send(400, {"error": "expected an object"})
            return

        try:
            if self.path == "/synctex":
                self._send(200, query_synctex(body))
            else:
                self._send(404, {"error": "not found"})
        except KeyError as exc:
            self._send(400, {"error": f"missing field {exc}"})
        except Exception:
            # The caller degrades on any non-answer; the detail stays here.
            self._send(500, {"error": "compile service failure"})

    def log_message(self, fmt: str, *args) -> None:
        # Default logging prints the full request line to stderr. The request
        # body is a user's LaTeX; keep it out of container logs.
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
