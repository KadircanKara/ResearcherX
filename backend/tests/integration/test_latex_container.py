"""End-to-end against the real compile container.

Skipped by default (`addopts = -m 'not container'`) because it needs the
service up and a real TeX Live. Run it explicitly:

    docker compose exec -T backend python -m pytest tests/integration -m container -v
"""

import base64
import io
import json
import socket
import tarfile
import time
from urllib.parse import urlparse

import httpx
import pytest

from app.core.config import settings
from app.services.latex_compiler import compile_source, synctex_forward, synctex_reverse

pytestmark = pytest.mark.container

# No COMPILER_URL constant exists in this file (the other tests reach the
# service through compile_source/synctex_forward/synctex_reverse, not raw
# HTTP) -- the tar tests below post directly, so they need the container's
# address themselves. settings.latex_compiler_url is the same value those
# helpers use.
COMPILER_URL = settings.latex_compiler_url

_PAPER = r"""
\documentclass[conference]{IEEEtran}
\begin{document}
\title{A Round Trip}
\author{Test}
\maketitle
\section{Introduction}
This sentence exists so a line maps to a box.
\end{document}
"""


async def test_a_real_template_compiles_and_round_trips_a_line():
    """Line -> page position -> line. The map survives being carried out of
    the build directory, which is the whole reason `synctex -d` is used."""
    result = await compile_source(_PAPER, "pdflatex")
    assert result.ok, result.log
    assert result.pdf and result.pdf.startswith(b"%PDF")
    assert result.synctex_gz

    # NOTE: derived from the exact (unstripped) string passed to
    # compile_source above -- _PAPER opens with a newline, so
    # \section{Introduction} lands on line 7 of the compiled file, not line
    # 6. Stripping here before indexing would measure a line that was never
    # compiled.
    line = _PAPER.splitlines().index(r"\section{Introduction}") + 1
    position = await synctex_forward(_PAPER, result.pdf, result.synctex_gz, line=line)
    assert position is not None
    assert position.page == 1

    back = await synctex_reverse(
        _PAPER, result.pdf, result.synctex_gz, page=position.page, x=position.x, y=position.y
    )
    assert back is not None
    # Exact equality is too strict: the client answers with the line of the
    # nearest box, which can be the paragraph's first line rather than the
    # heading. Within a couple of lines proves the map is real.
    assert abs(back - line) <= 3


async def test_the_same_template_also_compiles_under_xelatex():
    """Regression guard, not a bug hunt -- this currently passes. The
    integration suite was pdflatex-only, and the defect Task 7 caught
    (pdflatex could not build an IEEEtran conference paper) was precisely
    engine-specific breakage that nobody tested for the other engine this
    service offers."""
    result = await compile_source(_PAPER, "xelatex")

    assert result.ok, result.log
    assert result.pdf and result.pdf.startswith(b"%PDF")
    assert result.synctex_gz


async def test_a_broken_document_fails_with_a_log_and_no_pdf():
    result = await compile_source(
        r"\documentclass{article}\begin{document}\bogus\end{document}", "pdflatex"
    )

    assert not result.ok
    assert result.pdf is None
    assert "!" in result.log


async def test_the_compiler_has_no_secrets_to_leak():
    r"""`\input{}`-style file reads survive every engine flag, so the control
    is that the container holds nothing worth reading. That has to be tested
    with a REAL read: `\IfFileExists` only branches on whether a path
    exists, without reading a byte of it, so `assert b"LLM_API_KEY" not in
    pdf` used to be true BY CONSTRUCTION no matter what the container held --
    add `env_file:` to the compose service tomorrow and that old assertion
    would still pass.

    `\openin` + `\read` on /proc/self/environ pulls the real content. Its NUL
    separators between vars default to catcode 15 ("invalid character") and
    fatally halt compilation under `-halt-on-error` -- measured live -- so
    NUL is explicitly recatcoded to 9 ("ignored") before the read; that makes
    consecutive vars run together with no delimiter, which is harmless for a
    substring check. The dangerous metacharacters (`{`, `}`, `$`, `&`, `#`,
    `^`, `_`, `%`, `~`) are neutralised to catcode 12 ("other") for the same
    read, so whatever turns up is typeset as inert text, never interpreted as
    TeX. Compression is turned off so the shown text isn't buried inside a
    FlateDecode stream the raw-bytes assertions below cannot see.
    """
    probe = r"""
\pdfcompresslevel=0
\pdfobjcompresslevel=0
\documentclass{article}
\begin{document}
\begingroup
\catcode0=9
\catcode`\{=12 \catcode`\}=12
\catcode`\$=12 \catcode`\&=12 \catcode`\#=12
\catcode`\^=12 \catcode`\_=12 \catcode`\%=12 \catcode`\~=12
\openin0=/proc/self/environ
\read0 to \envcontents
\closein0
\ttfamily\obeyspaces
\envcontents
\endgroup
\end{document}
"""
    result = await compile_source(probe, "pdflatex")

    assert result.ok, result.log
    pdf = result.pdf or b""

    # CRITICAL: prove the read actually happened. TEXMFHOME is set by the
    # compiler image's OWN Dockerfile (`ENV TEXMFHOME=/tmp/texmf-home`), so
    # it is always present in this container's environment -- unlike a
    # secret, there is no scenario where a correctly-behaving container
    # lacks it. Without this assertion, a future change that silently
    # breaks the `\openin`/`\read` (a TeX Live update that restricts
    # filesystem access, a sandbox change that empties /proc, a typo that
    # makes the open fail) would make this test tautological again --
    # green forever, verifying nothing -- which is the exact defect this
    # rewrite exists to fix.
    assert b"TEXMFHOME" in pdf

    for secret in (b"LLM_API_KEY", b"DATABASE_URL", b"OWNER_API_KEY", b"POSTGRES_PASSWORD"):
        assert secret not in pdf
        assert secret not in result.log.encode()


async def test_a_broken_environ_read_would_be_caught_not_pass_tautologically():
    """Companion to test_the_compiler_has_no_secrets_to_leak: proves its
    TEXMFHOME assertion is a real guard, not a second tautology written on
    top of the first one. Pointing \\openin at a path that cannot exist on
    the compiler image simulates the read breaking -- a TeX Live upgrade
    that restricts filesystem access, a sandbox change that empties /proc,
    a typo in the probe. If that ever happens for real, the no-secrets
    test must FAIL, not stay green forever verifying nothing."""
    probe = r"""
\pdfcompresslevel=0
\pdfobjcompresslevel=0
\documentclass{article}
\begin{document}
\begingroup
\catcode0=9
\catcode`\{=12 \catcode`\}=12
\catcode`\$=12 \catcode`\&=12 \catcode`\#=12
\catcode`\^=12 \catcode`\_=12 \catcode`\%=12 \catcode`\~=12
\openin0=/this-path-cannot-exist-on-the-compiler-image
\read0 to \envcontents
\closein0
\ttfamily\obeyspaces
\envcontents
\endgroup
\end{document}
"""
    result = await compile_source(probe, "pdflatex")

    # A stream that never opened makes \read fall through to terminal
    # input, which -halt-on-error turns into a failed, PDF-less compile --
    # verified live. A broken read does not silently produce an
    # empty-but-successful build that could slip the TEXMFHOME assertion
    # by accident.
    assert not result.ok
    assert b"TEXMFHOME" not in (result.pdf or b"")


def _tar(entries: dict[str, bytes]) -> bytes:
    """Build an uncompressed tar in memory. `entries` maps arcname -> bytes."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


ROOT_DOC = b"""\\documentclass{article}
\\begin{document}
\\section{Root}
Alpha.
\\input{chapters/intro}
\\end{document}
"""
CHAPTER = b"\\section{Intro}\nBeta lives in a chapter.\n"

SELF_CONTAINED = b"""\\documentclass{article}
\\begin{document}
Alpha.
\\end{document}
"""


@pytest.mark.container
def test_a_multi_file_tree_compiles_with_its_input_resolved():
    body = _tar({"main.tex": ROOT_DOC, "chapters/intro.tex": CHAPTER})
    resp = httpx.post(
        f"{COMPILER_URL}/compile",
        content=body,
        headers={
            "Content-Type": "application/x-tar",
            "X-Engine": "pdflatex",
            "X-Main-Path": "main.tex",
        },
        timeout=120,
    )
    payload = resp.json()
    assert payload["ok"] is True, payload["log"]
    assert base64.b64decode(payload["pdf_b64"]).startswith(b"%PDF-")
    assert payload["synctex_b64"]
    assert payload["root"]


@pytest.mark.container
def test_a_main_file_in_a_subdirectory_compiles_and_its_artifacts_are_found():
    """latexmk -cd chdirs into the main file's directory and writes the PDF
    BESIDE it, not at the tree root. Measured; see the plan's table."""
    body = _tar({"src/paper.tex": ROOT_DOC, "src/chapters/intro.tex": CHAPTER})
    resp = httpx.post(
        f"{COMPILER_URL}/compile",
        content=body,
        headers={
            "Content-Type": "application/x-tar",
            "X-Engine": "pdflatex",
            "X-Main-Path": "src/paper.tex",
        },
        timeout=120,
    )
    payload = resp.json()
    assert payload["ok"] is True, payload["log"]
    assert base64.b64decode(payload["pdf_b64"]).startswith(b"%PDF-")


@pytest.mark.container
def test_a_missing_input_is_reported_as_a_tex_error_not_a_crash():
    body = _tar({"main.tex": ROOT_DOC})  # chapters/intro.tex deliberately absent
    resp = httpx.post(
        f"{COMPILER_URL}/compile",
        content=body,
        headers={
            "Content-Type": "application/x-tar",
            "X-Engine": "pdflatex",
            "X-Main-Path": "main.tex",
        },
        timeout=120,
    )
    payload = resp.json()
    assert payload["ok"] is False
    assert "intro" in payload["log"]


@pytest.mark.container
def test_a_tar_entry_escaping_the_tree_is_refused():
    """`_strict_filter` is our own refusal, layered on top of `data_filter`
    (the second, independent traversal guard) -- the first guard is
    latex_archive's validation, in a different process. SELF_CONTAINED (not
    ROOT_DOC) so a compile that succeeded would prove the entry was
    harmless, not merely that some unrelated missing \\input failed it."""
    body = _tar({"main.tex": SELF_CONTAINED, "../escape.tex": b"x"})
    resp = httpx.post(
        f"{COMPILER_URL}/compile",
        content=body,
        headers={
            "Content-Type": "application/x-tar",
            "X-Engine": "pdflatex",
            "X-Main-Path": "main.tex",
        },
        timeout=120,
    )
    assert resp.status_code in (200, 400)
    if resp.status_code == 200:
        payload = resp.json()
        assert payload["ok"] is False
        assert "unpacked" in payload["log"]


@pytest.mark.container
def test_an_absolute_tar_entry_is_refused():
    """`data_filter` alone would silently strip the leading slash and
    contain `/etc/passwd` at `<dest>/etc/passwd` -- accepted, not refused.
    SELF_CONTAINED so a compile that succeeded would prove that harmless
    containment, not merely that some unrelated missing \\input failed it."""
    body = _tar({"main.tex": SELF_CONTAINED, "/etc/passwd": b"x"})
    resp = httpx.post(
        f"{COMPILER_URL}/compile",
        content=body,
        headers={
            "Content-Type": "application/x-tar",
            "X-Engine": "pdflatex",
            "X-Main-Path": "main.tex",
        },
        timeout=120,
    )
    assert resp.status_code in (200, 400)
    if resp.status_code == 200:
        payload = resp.json()
        assert payload["ok"] is False
        assert "unpacked" in payload["log"]


@pytest.mark.container
def test_a_self_contained_document_with_no_hostile_entries_compiles():
    """Positive control for the two refusal tests above: without it, a
    blanket regression that fails every unpack (not just hostile ones) would
    pass this trio just as easily as the real guard does."""
    body = _tar({"main.tex": SELF_CONTAINED})
    resp = httpx.post(
        f"{COMPILER_URL}/compile",
        content=body,
        headers={
            "Content-Type": "application/x-tar",
            "X-Engine": "pdflatex",
            "X-Main-Path": "main.tex",
        },
        timeout=120,
    )
    payload = resp.json()
    assert payload["ok"] is True, payload["log"]


@pytest.mark.container
def test_a_main_path_outside_the_tar_is_refused():
    """`ok is False` alone is vacuous here: point X-Main-Path at a real file
    outside the tree (e.g. /etc/passwd) and latexmk runs on it, fails to
    typeset it, and reports ok:false too -- while ALSO echoing the file's
    content into the log via _first_error, an arbitrary-file-read reachable
    through a header instead of \\input. Pinning the log message is what
    tells the two apart: only the containment check produces this string."""
    body = _tar({"main.tex": ROOT_DOC})
    resp = httpx.post(
        f"{COMPILER_URL}/compile",
        content=body,
        headers={
            "Content-Type": "application/x-tar",
            "X-Engine": "pdflatex",
            "X-Main-Path": "../../etc/passwd",
        },
        timeout=120,
    )
    assert resp.status_code in (200, 400)
    if resp.status_code == 200:
        payload = resp.json()
        assert payload["ok"] is False
        assert "main file is not in the project" in payload["log"]


@pytest.mark.container
def test_the_json_compile_form_still_works():
    """Task 4 removes it; until then the backend still speaks JSON and every
    commit must leave the app working."""
    resp = httpx.post(
        f"{COMPILER_URL}/compile",
        json={
            "source": "\\documentclass{article}\\begin{document}x\\end{document}",
            "engine": "pdflatex",
        },
        timeout=120,
    )
    assert resp.json()["ok"] is True


def _raw_post(path: str, headers: dict[str, str], body: bytes, timeout: float = 40) -> bytes:
    """Post raw bytes over a plain socket, bypassing httpx entirely.

    httpx rejects control characters (including NUL) inside a header value
    before the request ever reaches the wire, so the NUL-byte test below has
    no other way to reach the server. Returns the full raw HTTP response (or
    b"" if the connection closed with nothing sent) rather than raising, so
    a dropped connection shows up as an assertion failure, not a socket
    exception with a stack trace that obscures what happened.
    """
    parsed = urlparse(COMPILER_URL)
    host, port = parsed.hostname, parsed.port or 80
    header_lines = "".join(f"{name}: {value}\r\n" for name, value in headers.items())
    request = (
        f"POST {path} HTTP/1.1\r\nHost: {host}\r\n{header_lines}"
        f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
    ).encode("latin-1")
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(request + body)
        sock.shutdown(socket.SHUT_WR)
        sock.settimeout(timeout)
        chunks = []
        while True:
            try:
                chunk = sock.recv(65536)
            except TimeoutError:
                break
            if not chunk:
                break
            chunks.append(chunk)
    return b"".join(chunks)


@pytest.mark.container
def test_a_nul_byte_in_the_main_path_header_gets_a_clean_response_not_a_dropped_connection():
    """A NUL in X-Main-Path reaches Path.resolve() as `ValueError: embedded
    null character in path`. If the tar dispatch sits outside do_POST's
    exception handler, that exception propagates out of the handler
    entirely: no HTTP response is ever written and the client sees a bare
    dropped connection -- the same failure mode the 413 branch's own
    comment calls indistinguishable from the service being down."""
    body = _tar({"main.tex": SELF_CONTAINED})
    headers = {
        "Content-Type": "application/x-tar",
        "X-Engine": "pdflatex",
        "X-Main-Path": "ma\x00in.tex",
    }
    response = _raw_post("/compile", headers, body)
    assert response, "connection dropped with no HTTP response"
    header, _, raw_body = response.partition(b"\r\n\r\n")
    assert header.startswith(b"HTTP/1."), header
    payload = json.loads(raw_body)
    assert isinstance(payload, dict)
    assert payload.get("ok") is not True


@pytest.mark.container
def test_a_lying_content_length_larger_than_the_body_sent_gets_a_clean_response_not_a_hang():
    """NOTE: this test is vacuous against any mutation of the drain/guard
    logic -- `sock.shutdown(SHUT_WR)` hands the server a clean EOF, so the
    response is guaranteed by socket semantics (a read past EOF returns b""
    immediately) rather than by anything the drain/guard code does. It
    passes unmodified even with `_Bounded`, the drain, and its try/except
    all removed. Kept as a baseline (a client that finishes and closes
    cleanly must still work), but
    test_an_over_declared_content_length_still_gets_a_response_when_the_client_stays_open
    below is the one that actually exercises the guard -- see its
    docstring."""
    body = _tar({"main.tex": SELF_CONTAINED})
    parsed = urlparse(COMPILER_URL)
    host, port = parsed.hostname, parsed.port or 80
    declared_length = len(body) + 4096
    request = (
        f"POST /compile HTTP/1.1\r\nHost: {host}\r\n"
        f"Content-Type: application/x-tar\r\nX-Engine: pdflatex\r\nX-Main-Path: main.tex\r\n"
        f"Content-Length: {declared_length}\r\nConnection: close\r\n\r\n"
    ).encode("latin-1")
    with socket.create_connection((host, port), timeout=40) as sock:
        sock.sendall(request + body)
        sock.shutdown(socket.SHUT_WR)  # no more bytes are coming, ever
        sock.settimeout(35)
        chunks = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    response = b"".join(chunks)
    assert response, "connection produced no HTTP response (hang or drop)"
    assert response.startswith(b"HTTP/1."), response[:200]


@pytest.mark.container
def test_an_over_declared_content_length_still_gets_a_response_when_the_client_stays_open():
    """The client sends a complete tar but declares more than it sends, and
    keeps the socket open -- no shutdown, no more data, exactly what an
    ordinary HTTP client does after writing its request and turning to read
    the response. This is the scenario that reproduced the bug: with the
    drain unguarded, the server's read for the (never-coming) declared
    remainder blocks until the connection's own socket timeout, then raises,
    and that exception used to propagate out of do_POST entirely -- no
    response, an empty connection. The fix wraps the drain in try/except and
    bounds it by MAX_DRAIN_BYTES (not by its own socket deadline -- an
    earlier version set connection.settimeout() here and never restored it,
    which silently truncated the RESPONSE itself for a slow reader on any
    request, tar or not; see MAX_DRAIN_BYTES's comment in app.py). Without a
    drain-specific timeout, a client that over-declares by this little
    (4096 bytes) can still make the drain's read wait up to the connection's
    own socket timeout before giving up -- so this asserts the response
    ARRIVES and is correct, not that it arrives quickly. Measured live: this
    takes roughly 30s (the connection's own socket timeout), which is exactly
    the tradeoff Finding 1 of round 4 chose: bounded-but-occasionally-slow
    over a socket deadline that leaked into unrelated code."""
    body = _tar({"main.tex": SELF_CONTAINED})
    parsed = urlparse(COMPILER_URL)
    host, port = parsed.hostname, parsed.port or 80
    declared_length = len(body) + 4096
    request = (
        f"POST /compile HTTP/1.1\r\nHost: {host}\r\n"
        f"Content-Type: application/x-tar\r\nX-Engine: pdflatex\r\nX-Main-Path: main.tex\r\n"
        f"Content-Length: {declared_length}\r\nConnection: close\r\n\r\n"
    ).encode("latin-1")
    started = time.monotonic()
    with socket.create_connection((host, port), timeout=40) as sock:
        sock.sendall(request + body)
        # Deliberately no shutdown(SHUT_WR) -- the socket stays open exactly
        # as a normal client leaves it while waiting for a response.
        sock.settimeout(35)
        chunks = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    elapsed = time.monotonic() - started
    response = b"".join(chunks)
    assert response, "connection produced no HTTP response (hang or drop)"
    header, _, raw_body = response.partition(b"\r\n\r\n")
    assert header.startswith(b"HTTP/1."), header
    assert b" 200 " in header.split(b"\r\n", 1)[0], header
    payload = json.loads(raw_body)
    assert payload["ok"] is True, payload.get("log")
    # Bounded, not instant: the connection's own 30s socket timeout is what
    # eventually gives up on the never-coming bytes. Comfortably under 35s
    # (the client's own recv timeout above) proves it's bounded at all.
    assert elapsed < 35, f"took {elapsed:.1f}s -- drain never gave up"


@pytest.mark.container
def test_a_20mb_tar_compiles_proving_the_tar_cap_is_32mb_not_16mb():
    """Nothing before this exercised a body between the 16MB JSON cap and
    the 32MB tar cap. The padding lives in a file the main document never
    references, so this only proves the SIZE cap, not compile correctness
    for large content."""
    body = _tar({"main.tex": SELF_CONTAINED, "pad.bin": b"\x00" * (20 * 1024 * 1024)})
    assert len(body) > 16 * 1024 * 1024
    resp = httpx.post(
        f"{COMPILER_URL}/compile",
        content=body,
        headers={
            "Content-Type": "application/x-tar",
            "X-Engine": "pdflatex",
            "X-Main-Path": "main.tex",
        },
        timeout=120,
    )
    payload = resp.json()
    assert payload["ok"] is True, payload["log"]


@pytest.mark.container
def test_a_33mb_tar_body_is_rejected_with_413():
    """The Content-Length check runs before any tar parsing, so this body
    does not need to be a well-formed archive."""
    body = b"x" * (33 * 1024 * 1024)
    resp = httpx.post(
        f"{COMPILER_URL}/compile",
        content=body,
        headers={
            "Content-Type": "application/x-tar",
            "X-Engine": "pdflatex",
            "X-Main-Path": "main.tex",
        },
        timeout=120,
    )
    assert resp.status_code == 413


@pytest.mark.container
def test_a_tar_over_the_member_cap_is_refused_before_extraction_finishes():
    """MAX_TAR_MEMBERS (4000) is the load-bearing half of the extraction
    cap -- inode amplification (each file costs a 4096-byte tmpfs page
    regardless of its declared size) is what a byte-total cap alone cannot
    catch. 4001 one-byte members plus a valid main.tex is a small tar (a
    few hundred KB), so this stays fast despite the member count."""
    entries = {"main.tex": SELF_CONTAINED}
    for i in range(4001):
        entries[f"pad/{i}.txt"] = b"x"
    body = _tar(entries)
    resp = httpx.post(
        f"{COMPILER_URL}/compile",
        content=body,
        headers={
            "Content-Type": "application/x-tar",
            "X-Engine": "pdflatex",
            "X-Main-Path": "main.tex",
        },
        timeout=120,
    )
    payload = resp.json()
    assert payload["ok"] is False
    assert "unpacked" in payload["log"]


@pytest.mark.container
def test_a_tar_with_4000_members_still_compiles():
    """Positive control pinning the OTHER side of the member-cap boundary:
    the previous test (4001 pad members, i.e. 4002 total with main.tex)
    only pins "a cap somewhere at or below 4002." Exactly 4000 members
    (main.tex + 3999 pad files) must still succeed, so the boundary is
    pinned on both sides."""
    entries = {"main.tex": SELF_CONTAINED}
    for i in range(3999):
        entries[f"pad/{i}.txt"] = b"x"
    assert len(entries) == 4000
    body = _tar(entries)
    resp = httpx.post(
        f"{COMPILER_URL}/compile",
        content=body,
        headers={
            "Content-Type": "application/x-tar",
            "X-Engine": "pdflatex",
            "X-Main-Path": "main.tex",
        },
        timeout=120,
    )
    payload = resp.json()
    assert payload["ok"] is True, payload["log"]


@pytest.mark.container
def test_trailing_bytes_under_an_honest_content_length_still_get_a_response():
    """Pins the drain's actual purpose (not the guard around it, which
    test_an_over_declared_content_length_... already covers): a valid tar
    followed by a substantial run of REAL trailing bytes, all of it
    genuinely sent by the client under a Content-Length that matches
    exactly what's sent (an honest header, unlike the lying-header tests
    above). `compile_tree` stops reading at the tar's own end-of-archive
    marker, so those trailing bytes are real, already-sent data sitting
    unread in the kernel receive buffer when the server responds and
    closes -- round 2 measured this exact shape produces a TCP RST that
    swallows the response entirely (BrokenPipeError, no response
    delivered) when nothing drains them first.

    10MB here, not 2MB: round 5 found that MAX_DRAIN_BYTES=1MB (round 4's
    value) only narrowed this bug instead of eliminating it -- 5MB of real
    trailing bytes reproduced the RST at that size, and this test's own
    2MB was close enough to the old bound to flake (3 passes / 2 failures
    in 5 runs against that value). MAX_DRAIN_BYTES is now MAX_TAR_LENGTH
    (32MB, see its comment in app.py), so 10MB is comfortably inside the
    bound with no ambiguity -- this is a determinism fix for the test, not
    a claim that a specific byte count is special."""
    body = _tar({"main.tex": SELF_CONTAINED})
    trailing = b"\x00" * (10 * 1024 * 1024)  # real bytes, actually sent
    full_body = body + trailing
    parsed = urlparse(COMPILER_URL)
    host, port = parsed.hostname, parsed.port or 80
    request = (
        f"POST /compile HTTP/1.1\r\nHost: {host}\r\n"
        f"Content-Type: application/x-tar\r\nX-Engine: pdflatex\r\nX-Main-Path: main.tex\r\n"
        f"Content-Length: {len(full_body)}\r\nConnection: close\r\n\r\n"
    ).encode("latin-1")
    with socket.create_connection((host, port), timeout=60) as sock:
        sock.sendall(request + full_body)
        sock.settimeout(50)
        chunks = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    response = b"".join(chunks)
    assert response, "connection produced no HTTP response (RST swallowed it)"
    header, _, raw_body = response.partition(b"\r\n\r\n")
    assert header.startswith(b"HTTP/1."), header
    assert b" 200 " in header.split(b"\r\n", 1)[0], header
    payload = json.loads(raw_body)
    assert payload["ok"] is True, payload.get("log")


@pytest.mark.container
def test_sync_round_trips_a_line_in_a_chapter_file():
    """Forward from a chapter, then reverse back to it. The chapter is the
    case that single-file sync could not express at all."""
    body = _tar({"main.tex": ROOT_DOC, "chapters/intro.tex": CHAPTER})
    compiled = httpx.post(
        f"{COMPILER_URL}/compile",
        content=body,
        headers={
            "Content-Type": "application/x-tar",
            "X-Engine": "pdflatex",
            "X-Main-Path": "main.tex",
        },
        timeout=120,
    ).json()
    assert compiled["ok"] is True, compiled["log"]

    common = {
        "pdf_b64": compiled["pdf_b64"],
        "synctex_b64": compiled["synctex_b64"],
        "main_path": "main.tex",
        "root": compiled["root"],
    }
    fwd = httpx.post(
        f"{COMPILER_URL}/synctex",
        json={**common, "direction": "forward", "file": "chapters/intro.tex", "line": 2},
        timeout=60,
    ).json()
    assert fwd["found"] is True

    rev = httpx.post(
        f"{COMPILER_URL}/synctex",
        json={**common, "direction": "reverse", "page": fwd["page"], "x": fwd["x"], "y": fwd["y"]},
        timeout=60,
    ).json()
    assert rev["found"] is True
    assert rev["file"] == "chapters/intro.tex"
    assert rev["line"] == 2


@pytest.mark.container
def test_sync_round_trips_when_the_main_file_is_in_a_subdirectory():
    """synctex speaks paths relative to the MAIN FILE'S DIRECTORY, not the
    tree root -- measured. The wire protocol is tree-relative and the
    compiler converts, so this must work identically to the flat case."""
    body = _tar({"src/paper.tex": ROOT_DOC, "src/chapters/intro.tex": CHAPTER})
    compiled = httpx.post(
        f"{COMPILER_URL}/compile",
        content=body,
        headers={
            "Content-Type": "application/x-tar",
            "X-Engine": "pdflatex",
            "X-Main-Path": "src/paper.tex",
        },
        timeout=120,
    ).json()
    assert compiled["ok"] is True, compiled["log"]

    common = {
        "pdf_b64": compiled["pdf_b64"],
        "synctex_b64": compiled["synctex_b64"],
        "main_path": "src/paper.tex",
        "root": compiled["root"],
    }
    fwd = httpx.post(
        f"{COMPILER_URL}/synctex",
        json={**common, "direction": "forward", "file": "src/chapters/intro.tex", "line": 2},
        timeout=60,
    ).json()
    assert fwd["found"] is True

    rev = httpx.post(
        f"{COMPILER_URL}/synctex",
        json={**common, "direction": "reverse", "page": fwd["page"], "x": fwd["x"], "y": fwd["y"]},
        timeout=60,
    ).json()
    assert rev["found"] is True
    assert rev["file"] == "src/chapters/intro.tex"
    assert rev["line"] == 2


@pytest.mark.container
def test_reverse_sync_in_the_main_file_reports_the_main_file():
    body = _tar({"main.tex": ROOT_DOC, "chapters/intro.tex": CHAPTER})
    compiled = httpx.post(
        f"{COMPILER_URL}/compile",
        content=body,
        headers={
            "Content-Type": "application/x-tar",
            "X-Engine": "pdflatex",
            "X-Main-Path": "main.tex",
        },
        timeout=120,
    ).json()
    common = {
        "pdf_b64": compiled["pdf_b64"],
        "synctex_b64": compiled["synctex_b64"],
        "main_path": "main.tex",
        "root": compiled["root"],
    }
    fwd = httpx.post(
        f"{COMPILER_URL}/synctex",
        json={**common, "direction": "forward", "file": "main.tex", "line": 4},
        timeout=60,
    ).json()
    assert fwd["found"] is True
    rev = httpx.post(
        f"{COMPILER_URL}/synctex",
        json={**common, "direction": "reverse", "page": fwd["page"], "x": fwd["x"], "y": fwd["y"]},
        timeout=60,
    ).json()
    assert rev["found"] is True
    assert rev["file"] == "main.tex"
    # NOT an exact line assertion: lines 3 and 4 of this document produce the
    # identical synctex box (measured), so reverse returns the first of them
    # for either. Node merging is a property of the document, not of the
    # conversion under test -- and this test is named for the FILE. The
    # chapter round-trip tests do pin an exact line, where the boxes are
    # distinct.
    assert rev["line"] in (3, 4)


@pytest.mark.container
def test_sync_needs_no_source_files_at_all():
    """Measured in plan 1: both directions answer from the PDF and the map
    alone -- so a stray `source` key in the payload (the old client's shape,
    or a caller that has not been updated yet) must be IGNORED, not consulted.
    A payload that merely omits `source` cannot tell the two apart; sending a
    deliberately wrong one and comparing to the no-source answer can."""
    body = _tar({"main.tex": ROOT_DOC, "chapters/intro.tex": CHAPTER})
    compiled = httpx.post(
        f"{COMPILER_URL}/compile",
        content=body,
        headers={
            "Content-Type": "application/x-tar",
            "X-Engine": "pdflatex",
            "X-Main-Path": "main.tex",
        },
        timeout=120,
    ).json()
    payload = {
        "pdf_b64": compiled["pdf_b64"],
        "synctex_b64": compiled["synctex_b64"],
        "main_path": "main.tex",
        "root": compiled["root"],
        "direction": "forward",
        "file": "chapters/intro.tex",
        "line": 2,
    }
    baseline = httpx.post(f"{COMPILER_URL}/synctex", json=payload, timeout=60).json()
    assert baseline["found"] is True

    poisoned = {
        **payload,
        "source": "\\documentclass{article}\\begin{document}wrong\\end{document}",
    }
    answer = httpx.post(f"{COMPILER_URL}/synctex", json=poisoned, timeout=60).json()
    assert answer == baseline


@pytest.mark.container
def test_the_legacy_single_file_synctex_contract_still_works():
    """Compatibility shim, scoped to last exactly as long as the pre-tree
    backend client does: Task 3 switches that client to main_path/file/root,
    and Task 4 deletes this test along with the fallback in query_synctex.

    The pre-existing client (`app/services/latex_compiler.py`) posts `source`
    and no `main_path`/`file`/`root` at all -- the single-file contract that
    predates the tree. `main_path` absent must still resolve against the
    compiled `master.tex`/`master.pdf`/`master.synctex.gz` triple, exactly as
    it did before this task's rewrite."""
    compiled = httpx.post(
        f"{COMPILER_URL}/compile",
        json={"source": SELF_CONTAINED.decode(), "engine": "pdflatex"},
        timeout=120,
    ).json()
    assert compiled["ok"] is True, compiled["log"]

    payload = {
        "source": SELF_CONTAINED.decode(),
        "pdf_b64": compiled["pdf_b64"],
        "synctex_b64": compiled["synctex_b64"],
        "direction": "forward",
        "line": 3,
    }
    assert "main_path" not in payload
    assert "file" not in payload
    fwd = httpx.post(f"{COMPILER_URL}/synctex", json=payload, timeout=60).json()
    assert fwd["found"] is True


@pytest.mark.container
def test_reverse_sync_resolves_a_file_reached_through_a_parent_relative_input():
    """Pins the root-strip in `_tree_path`: a mutant deleting it passed every
    other test here, because every other test's main file sits at the tree
    root (main_dir == "") where the strip is a no-op. A subdirectory main
    file whose own `\\input` escapes it (`\\input{../shared/x}`, cwd == the
    main file's directory because of `latexmk -cd`) records
    `Input:<root>/src/../shared/x.tex` -- measured, the exact shape in the
    corrected design's own example table -- and only the strip plus `../`
    normalisation together turn that into the tree-relative `shared/x.tex`
    rather than `src/../shared/x.tex` or nothing at all.

    Forward sync cannot reach this box (documented asymmetry: `shared/x.tex`
    is unaddressable via `file=` from a `src/` main -- `relpath` yields a
    `../`-prefixed path and synctex's `view` returns NOT FOUND for it), so
    the page/x/y here are measured directly against the compiled PDF -- by
    scanning `synctex edit` across the page -- rather than obtained through a
    forward query."""
    main = b"""\\documentclass{article}
\\begin{document}
\\section{Root}
Alpha.
\\input{../shared/x}
\\end{document}
"""
    shared = (
        b"\\section{Shared}\n"
        b"Gamma lives outside src, and this is a longer sentence so it gets its "
        b"own paragraph box distinct from the includer line, hopefully producing "
        b"a synctex node whose Input points at this file rather than the "
        b"includer.\n"
    )
    body = _tar({"src/paper.tex": main, "shared/x.tex": shared})
    compiled = httpx.post(
        f"{COMPILER_URL}/compile",
        content=body,
        headers={
            "Content-Type": "application/x-tar",
            "X-Engine": "pdflatex",
            "X-Main-Path": "src/paper.tex",
        },
        timeout=120,
    ).json()
    assert compiled["ok"] is True, compiled["log"]

    rev = httpx.post(
        f"{COMPILER_URL}/synctex",
        json={
            "pdf_b64": compiled["pdf_b64"],
            "synctex_b64": compiled["synctex_b64"],
            "main_path": "src/paper.tex",
            "root": compiled["root"],
            "direction": "reverse",
            "page": 1,
            "x": 100,
            "y": 170,
        },
        timeout=60,
    ).json()
    assert rev["found"] is True
    assert rev["file"] == "shared/x.tex"
    assert rev["line"] == 1


@pytest.mark.container
def test_an_unrecognised_direction_is_rejected_rather_than_falling_through_to_reverse():
    """`direction` is attacker-controlled JSON. Anything other than the two
    known values must be a clean miss, not a silent fall-through into the
    `else` branch that used to mean "reverse" -- which would try to build a
    `synctex edit` command out of a forward-shaped payload (no page/x/y) and
    KeyError, or worse, coincidentally succeed on stale defaults."""
    body = _tar({"main.tex": ROOT_DOC, "chapters/intro.tex": CHAPTER})
    compiled = httpx.post(
        f"{COMPILER_URL}/compile",
        content=body,
        headers={
            "Content-Type": "application/x-tar",
            "X-Engine": "pdflatex",
            "X-Main-Path": "main.tex",
        },
        timeout=120,
    ).json()
    assert compiled["ok"] is True, compiled["log"]

    answer = httpx.post(
        f"{COMPILER_URL}/synctex",
        json={
            "pdf_b64": compiled["pdf_b64"],
            "synctex_b64": compiled["synctex_b64"],
            "main_path": "main.tex",
            "root": compiled["root"],
            "direction": "sideways",
            "line": 2,
        },
        timeout=60,
    ).json()
    assert answer == {"found": False}


@pytest.mark.container
def test_a_non_string_main_path_root_or_file_is_treated_as_absent_not_a_500():
    """`main_path`/`root`/`file` arrive as untyped JSON. A list, number, or
    null must fall back to the legacy default rather than reaching a string
    operation (`posixpath.dirname`, `.startswith`, string concatenation) and
    raising -- which do_POST's generic except turns into a 500, but a 500
    here means the double-click silently does nothing instead of a clean
    `found: false`. `main_path` and `file` are exercised on a forward query
    (both feed `posixpath.relpath`/`.dirname`); `root` only matters on
    reverse, since it is `_tree_path`'s alone."""
    body = _tar({"main.tex": ROOT_DOC, "chapters/intro.tex": CHAPTER})
    compiled = httpx.post(
        f"{COMPILER_URL}/compile",
        content=body,
        headers={
            "Content-Type": "application/x-tar",
            "X-Engine": "pdflatex",
            "X-Main-Path": "main.tex",
        },
        timeout=120,
    ).json()
    assert compiled["ok"] is True, compiled["log"]
    common = {
        "pdf_b64": compiled["pdf_b64"],
        "synctex_b64": compiled["synctex_b64"],
        "main_path": "main.tex",
        "root": compiled["root"],
    }

    for bad in ({"main_path": 4}, {"file": None}):
        payload = {**common, "direction": "forward", "file": "main.tex", "line": 3, **bad}
        response = httpx.post(f"{COMPILER_URL}/synctex", json=payload, timeout=60)
        assert response.status_code == 200, (bad, response.text)

    fwd = httpx.post(
        f"{COMPILER_URL}/synctex",
        json={**common, "direction": "forward", "file": "main.tex", "line": 3},
        timeout=60,
    ).json()
    assert fwd["found"] is True
    rev_response = httpx.post(
        f"{COMPILER_URL}/synctex",
        json={
            **common,
            "root": ["not", "a", "string"],
            "direction": "reverse",
            "page": fwd["page"],
            "x": fwd["x"],
            "y": fwd["y"],
        },
        timeout=60,
    )
    assert rev_response.status_code == 200, rev_response.text
    assert rev_response.json() == {"found": False}
