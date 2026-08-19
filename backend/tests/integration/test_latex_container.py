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
    """`_Bounded` is defence in depth against tarfile reading past the
    declared body; this is the mirror case -- a Content-Length claiming MORE
    than the client actually sends must not hang the server waiting for
    bytes that are never coming. Shutting down the write half of the socket
    right after the real (short) body simulates exactly that: the server's
    next read sees a clean EOF, not a stall."""
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
