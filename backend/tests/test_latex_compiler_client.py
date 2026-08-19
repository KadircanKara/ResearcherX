"""Client for the sandboxed compile service. The HTTP layer is mocked; the
container is exercised in the integration task."""

import asyncio
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
    # The POINT of this test: the PDF survives a missing map. Asserting only
    # that synctex_gz is None would pass with pdf wrongly None too.
    assert result.pdf == b"%PDF-1.5"
    assert result.synctex_gz is None


async def test_malformed_base64_degrades_instead_of_raising():
    """A truncated body is a failed compile, not a 500 out of a chat turn."""
    payload = {
        "ok": True,
        "log": "",
        "pdf_b64": "not-valid-base64!!",
        "synctex_b64": None,
    }
    with patch("httpx.AsyncClient", return_value=_client_returning(payload)):
        result = await compile_source("\\documentclass{article}", "pdflatex")

    assert not result.ok
    assert result.pdf is None


async def test_a_non_2xx_response_degrades():
    class _Failing(_Response):
        def raise_for_status(self):
            import httpx

            raise httpx.HTTPStatusError("boom", request=None, response=None)

    client = MagicMock()
    client.post = AsyncMock(return_value=_Failing({}))
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False

    with patch("httpx.AsyncClient", return_value=client):
        result = await compile_source("\\documentclass{article}", "pdflatex")

    assert not result.ok
    assert "boom" not in result.log


async def test_a_malformed_json_body_degrades():
    class _BadJson(_Response):
        def json(self):
            raise ValueError("not json")

    client = MagicMock()
    client.post = AsyncMock(return_value=_BadJson({}))
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False

    with patch("httpx.AsyncClient", return_value=client):
        result = await compile_source("\\documentclass{article}", "pdflatex")

    assert not result.ok
    assert "not json" not in result.log


async def test_found_true_without_the_other_keys_degrades_to_no_navigation():
    """The client and the service are separately deployed images; a version
    skew must cost navigation, never raise."""
    with patch("httpx.AsyncClient", return_value=_client_returning({"found": True})):
        assert await synctex_forward("src", b"pdf", b"gz", line=1) is None
    with patch("httpx.AsyncClient", return_value=_client_returning({"found": True})):
        assert await synctex_reverse("src", b"pdf", b"gz", page=1, x=0.0, y=0.0) is None


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


async def test_concurrent_compiles_are_bounded_by_the_semaphore():
    """A fresh Semaphore(2) for the test -- the point under test is that a
    third concurrent compile has to wait for one of the first two to finish,
    not that the production default (8) is exactly right. Without the
    semaphore in compile_source, all four tasks below would enter `_post`
    immediately and `entered` would reach 4, not 2."""
    limit = asyncio.Semaphore(2)
    entered = 0
    max_entered = 0
    release = asyncio.Event()
    entered_two = asyncio.Event()

    async def fake_post(path, payload):
        nonlocal entered, max_entered
        entered += 1
        max_entered = max(max_entered, entered)
        if entered == 2:
            entered_two.set()
        await release.wait()
        entered -= 1
        return {"ok": True, "log": "", "pdf_b64": None, "synctex_b64": None}

    with (
        patch("app.services.latex_compiler._compile_semaphore", limit),
        patch("app.services.latex_compiler._post", fake_post),
    ):
        tasks = [asyncio.create_task(compile_source("x", "pdflatex")) for _ in range(4)]
        await asyncio.wait_for(entered_two.wait(), timeout=2)
        # Yield once so a wrongly-admitted third task would have a chance to
        # run before we check.
        await asyncio.sleep(0)
        assert entered == 2

        release.set()
        results = await asyncio.gather(*tasks)

    assert max_entered == 2
    assert all(r.ok for r in results)


async def test_synctex_calls_are_not_gated_by_the_compile_semaphore():
    """synctex is short and cheap and does not spawn latexmk -- it must stay
    outside the compile gate, or a burst of navigation clicks would queue
    behind unrelated compiles for no reason. The holder below occupies the
    semaphore's only slot for far longer than synctex's own timeout below,
    so if a future change wrongly wraps synctex_forward in the same
    semaphore, this test TIMES OUT instead of passing by luck."""
    limit = asyncio.Semaphore(1)
    payload = {"found": True, "page": 1, "x": 1.0, "y": 1.0, "width": 1.0, "height": 1.0}

    async def hold_compile_semaphore():
        async with limit:
            await asyncio.sleep(10)

    with (
        patch("app.services.latex_compiler._compile_semaphore", limit),
        patch("httpx.AsyncClient", return_value=_client_returning(payload)),
    ):
        holder = asyncio.create_task(hold_compile_semaphore())
        await asyncio.sleep(0)  # let it acquire the semaphore's only slot first
        position = await asyncio.wait_for(
            synctex_forward("src", b"pdf", b"gz", line=1), timeout=1
        )
        holder.cancel()
        try:
            await holder
        except asyncio.CancelledError:
            pass

    assert position is not None
