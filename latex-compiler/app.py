"""LaTeX compilation, isolated from everything worth stealing.

This service exists because compiling user-authored LaTeX is arbitrary code
execution and the engine flags do not close it:

- `-no-shell-escape` blocks `\\write18`, but not LuaTeX's `\\directlua`.
  LuaTeX's `--safer` would, and it aborts every real compile of a normal
  preamble (`luaotfload.lua:105: error("safer_option used")`) -- so the flag
  that would help is unusable.
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
import subprocess
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Wall-clock ceiling for one compile. A runaway document is the common case
# (a recursive macro), not the rare one.
COMPILE_TIMEOUT = 30
SYNCTEX_TIMEOUT = 10

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

_MASTER = "master.tex"


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


def compile_tex(body: dict) -> dict:
    """`body`: {"source": str, "engine": str}. Unknown engines fall back to
    pdflatex rather than erroring: an engine string is a stored column, not
    user prose, and a failed compile is a worse answer than the default."""
    flag = _ENGINE_FLAG.get(body.get("engine", "pdflatex"), "-pdf")
    with tempfile.TemporaryDirectory(prefix="rx-latex-") as tmp:
        directory = Path(tmp)
        (directory / _MASTER).write_text(body.get("source", ""), encoding="utf-8")
        try:
            proc = subprocess.run(
                [
                    "latexmk",
                    flag,
                    "-synctex=1",
                    "-interaction=nonstopmode",
                    "-no-shell-escape",
                    "-halt-on-error",
                    _MASTER,
                ],
                cwd=directory,
                capture_output=True,
                text=True,
                timeout=COMPILE_TIMEOUT,
            )
            log_text = proc.stdout + proc.stderr
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "log": f"Compilation exceeded {COMPILE_TIMEOUT}s and was stopped.",
                "pdf_b64": None,
                "synctex_b64": None,
            }

        log_file = directory / "master.log"
        if log_file.exists():
            log_text = log_file.read_text(encoding="utf-8", errors="replace")

        pdf = directory / "master.pdf"
        synctex = directory / "master.synctex.gz"
        if not pdf.exists():
            return {
                "ok": False,
                "log": _first_error(log_text),
                "pdf_b64": None,
                "synctex_b64": None,
            }
        return {
            "ok": True,
            "log": _first_error(log_text) if proc.returncode else "",
            "pdf_b64": base64.b64encode(pdf.read_bytes()).decode(),
            # A compile can succeed without a map (an engine that ignored
            # -synctex=1). Navigation is an enhancement; the PDF still ships.
            "synctex_b64": (
                base64.b64encode(synctex.read_bytes()).decode() if synctex.exists() else None
            ),
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
    """`body`: source, pdf_b64, synctex_b64, direction, and the coordinates
    for that direction."""
    direction = body.get("direction")
    with tempfile.TemporaryDirectory(prefix="rx-synctex-") as tmp:
        directory = Path(tmp)
        (directory / _MASTER).write_text(body.get("source", ""), encoding="utf-8")
        (directory / "master.pdf").write_bytes(base64.b64decode(body["pdf_b64"]))
        (directory / "master.synctex.gz").write_bytes(base64.b64decode(body["synctex_b64"]))
        pdf = directory / "master.pdf"

        # `-d <dir>` is load-bearing: the map records ABSOLUTE paths from the
        # directory it was compiled in, which no longer exists by the time
        # anyone queries it. Verified in jobfit against a real document --
        # both view and edit answer correctly from a directory the document
        # was never compiled in.
        if direction == "forward":
            args = [
                "synctex", "view",
                "-i", f"{body['line']}:0:{_MASTER}",
                "-o", str(pdf),
                "-d", str(directory),
            ]
        else:
            args = [
                "synctex", "edit",
                "-o", f"{body['page']}:{body['x']}:{body['y']}:{pdf}",
                "-d", str(directory),
            ]
        try:
            proc = subprocess.run(
                args, capture_output=True, text=True, timeout=SYNCTEX_TIMEOUT
            )
        except subprocess.TimeoutExpired:
            return {"found": False}

        records = _records(proc.stdout)
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
        # `Input:` is NOT trustworthy after staging -- the client echoes the
        # original compile-time path, a stale string naming a deleted
        # directory. Only `Line:` is re-derived, which is why a document is
        # one file in v1.
        lines = [r for r in records if "Line" in r]
        if not lines:
            return {"found": False}
        return {"found": True, "line": int(lines[0]["Line"])}


class Handler(BaseHTTPRequestHandler):
    """Two POST routes and a health check, on the standard library.

    ThreadingHTTPServer, not the single-threaded default: two members
    compiling at once would otherwise serialise behind a 30-second run, and
    the container's `pids_limit` is what bounds concurrency instead.
    """

    server_version = "latex-compiler"

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(200, {"ok": True})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send(400, {"error": "invalid json"})
            return
        if not isinstance(body, dict):
            self._send(400, {"error": "expected an object"})
            return

        try:
            if self.path == "/compile":
                self._send(200, compile_tex(body))
            elif self.path == "/synctex":
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
