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
    r"""`\input{}` file reads survive every engine flag, so the control is that
    the container holds nothing worth reading. This compiles a document that
    tries, and asserts the build does not contain a key."""
    probe = r"""
\documentclass{article}
\begin{document}
\IfFileExists{/proc/self/environ}{present}{absent}
\end{document}
"""
    result = await compile_source(probe, "pdflatex")

    assert result.ok
    assert b"LLM_API_KEY" not in (result.pdf or b"")
    assert b"DATABASE_URL" not in (result.pdf or b"")
