"""Client for the sandboxed compile service. The HTTP layer is mocked; the
container is exercised in the integration task."""

import base64
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.latex_compiler import (
    compile_source,
    synctex_forward,
    synctex_reverse,
)


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _client_returning(payload):
    """A stand-in for httpx.AsyncClient used as an async context manager.

    MagicMock, not AsyncMock, and `__aenter__.return_value` rather than
    assigning `__aenter__`: `async with obj` resolves the dunder on the TYPE,
    so configuring the auto-created async magic is what actually takes effect.
    __aenter__ must yield the SAME object whose .post is stubbed, or the code
    under test calls a different mock than the test configured.
    """
    client = MagicMock()
    client.post = AsyncMock(return_value=_Response(payload))
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    return client


async def test_a_successful_compile_decodes_both_artifacts():
    payload = {
        "ok": True,
        "log": "",
        "pdf_b64": base64.b64encode(b"%PDF-1.5").decode(),
        "synctex_b64": base64.b64encode(b"gzbytes").decode(),
    }
    with patch("httpx.AsyncClient", return_value=_client_returning(payload)):
        result = await compile_source("\\documentclass{article}", "pdflatex")

    assert result.ok
    assert result.pdf == b"%PDF-1.5"
    assert result.synctex_gz == b"gzbytes"


async def test_a_failed_compile_carries_the_log_and_no_pdf():
    payload = {
        "ok": False,
        "log": "! Undefined control sequence.",
        "pdf_b64": None,
        "synctex_b64": None,
    }
    with patch("httpx.AsyncClient", return_value=_client_returning(payload)):
        result = await compile_source("\\bogus", "pdflatex")

    assert not result.ok
    assert result.pdf is None
    assert "Undefined control sequence" in result.log


async def test_a_compile_without_a_map_still_returns_the_pdf():
    """Navigation is an enhancement. An engine that ignored -synctex=1 must
    not cost the user their PDF."""
    payload = {
        "ok": True,
        "log": "",
        "pdf_b64": base64.b64encode(b"%PDF-1.5").decode(),
        "synctex_b64": None,
    }
    with patch("httpx.AsyncClient", return_value=_client_returning(payload)):
        result = await compile_source("\\documentclass{article}", "pdflatex")

    assert result.ok
    assert result.synctex_gz is None


async def test_an_unreachable_compiler_fails_open_to_a_generic_message():
    """The service being down is a server internal. The user sees a generic
    line, never the exception text."""
    import httpx

    client = MagicMock()
    client.post = AsyncMock(side_effect=httpx.ConnectError("nope"))
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False

    with patch("httpx.AsyncClient", return_value=client):
        result = await compile_source("\\documentclass{article}", "pdflatex")

    assert not result.ok
    assert result.pdf is None
    assert "nope" not in result.log


async def test_forward_sync_returns_a_position():
    payload = {"found": True, "page": 1, "x": 36.0, "y": 122.0, "width": 100.0, "height": 12.0}
    with patch("httpx.AsyncClient", return_value=_client_returning(payload)):
        position = await synctex_forward("src", b"pdf", b"gz", line=161)

    assert position.page == 1
    assert position.x == 36.0
    assert position.y == 122.0


async def test_forward_sync_returns_none_when_the_map_has_no_answer():
    with patch("httpx.AsyncClient", return_value=_client_returning({"found": False})):
        assert await synctex_forward("src", b"pdf", b"gz", line=1) is None


async def test_reverse_sync_returns_a_line():
    with patch("httpx.AsyncClient", return_value=_client_returning({"found": True, "line": 161})):
        assert await synctex_reverse("src", b"pdf", b"gz", page=1, x=36.0, y=122.0) == 161


async def test_reverse_sync_returns_none_when_unavailable():
    with patch("httpx.AsyncClient", return_value=_client_returning({"found": False})):
        assert await synctex_reverse("src", b"pdf", b"gz", page=1, x=0.0, y=0.0) is None
