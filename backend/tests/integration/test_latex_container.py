"""End-to-end against the real compile container.

Skipped by default (`addopts = -m 'not container'`) because it needs the
service up and a real TeX Live. Run it explicitly:

    docker compose exec -T backend python -m pytest tests/integration -m container -v
"""

import pytest

from app.services.latex_compiler import compile_source, synctex_forward, synctex_reverse

pytestmark = pytest.mark.container

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
