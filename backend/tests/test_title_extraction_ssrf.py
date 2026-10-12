"""`extract_meta_from_page` fetches a member's URL and returns what it read,
so it carries the same guard as `fetch_pdf`: every hop is checked before it
is requested, and a PDF body is read against the byte cap."""

import httpx
import pytest

from app.services import title_extraction_service as svc

_HTML = b"<html><head><title>Internal admin console</title></head></html>"


@pytest.fixture
def transport(monkeypatch):
    """Route the service's httpx client through a recording mock transport."""
    seen: list[str] = []
    routes: dict[str, httpx.Response] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return routes.get(str(request.url), httpx.Response(200, content=_HTML))

    real = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(svc.httpx, "AsyncClient", client)
    return seen, routes


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8787/v1/models",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/",
        "file:///etc/passwd",
    ],
)
async def test_a_non_public_url_is_never_requested(transport, url):
    seen, _ = transport
    assert await svc.extract_meta_from_page(url) == (None, None)
    assert seen == []


async def test_a_redirect_into_the_internal_network_is_not_followed(transport):
    seen, routes = transport
    start = "http://93.184.216.34/paper"
    routes[start] = httpx.Response(302, headers={"location": "http://169.254.169.254/iam/"})
    assert await svc.extract_meta_from_page(start) == (None, None)
    assert seen == [start]


async def test_a_pdf_body_over_the_cap_is_not_parsed(transport, monkeypatch):
    from app.core.config import settings

    seen, routes = transport
    url = "http://93.184.216.34/big.pdf"
    routes[url] = httpx.Response(
        200, headers={"content-type": "application/pdf"}, content=b"%PDF" + b"x" * 2048
    )
    monkeypatch.setattr(settings, "paper_pdf_max_bytes", 1024)
    parsed = []

    async def fake_parse(data):
        parsed.append(len(data))
        return "t", "a", "b"

    monkeypatch.setattr(svc, "extract_meta_from_pdf", fake_parse)
    assert await svc.extract_meta_from_page(url) == (None, None)
    assert parsed == []


async def test_a_public_page_still_yields_its_title(transport):
    assert await svc.extract_meta_from_page("http://93.184.216.34/paper") == (
        "Internal admin console",
        None,
    )
