"""The guard that stops a pasted URL reaching our own network.

`fetch_pdf` retrieves a user-supplied URL from the server, and its extracted
text is persisted on the paper -- so an unguarded fetch is SSRF with a
readable channel back, whether or not the PDF itself is stored.
"""

import ipaddress
import socket

import pytest

from app.services import paper_fetch_service as svc


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",  # loopback
        "10.0.0.5",  # private
        "192.168.1.1",  # private
        "172.16.0.1",  # private
        "169.254.169.254",  # link-local: the cloud metadata endpoint
        "0.0.0.0",  # unspecified
        "224.0.0.1",  # multicast
        "::1",  # loopback, v6
        "fc00::1",  # unique-local, v6
    ],
)
def test_non_public_addresses_are_blocked(address: str):
    assert svc._is_blocked(ipaddress.ip_address(address)) is True


@pytest.mark.parametrize("address", ["8.8.8.8", "1.1.1.1", "2606:4700::1111"])
def test_public_addresses_are_allowed(address: str):
    assert svc._is_blocked(ipaddress.ip_address(address)) is False


async def test_a_non_http_scheme_is_refused():
    # file:// would read the server's own disk; gopher:// and friends are
    # classic SSRF protocol-smuggling vectors.
    with pytest.raises(svc.UnsafeUrl):
        await svc.assert_fetchable("file:///etc/passwd")


async def test_a_url_with_no_host_is_refused():
    with pytest.raises(svc.UnsafeUrl):
        await svc.assert_fetchable("http:///nowhere")


async def test_a_hostname_resolving_to_loopback_is_refused(monkeypatch):
    """The reason the check runs AFTER resolution: a name an attacker owns
    can simply point at 127.0.0.1, so validating the hostname protects
    nothing."""

    async def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))]

    monkeypatch.setattr(
        "asyncio.get_running_loop",
        lambda: type("L", (), {"getaddrinfo": staticmethod(fake_getaddrinfo)})(),
    )
    with pytest.raises(svc.UnsafeUrl):
        await svc.assert_fetchable("http://evil.example.com/paper.pdf")


async def test_a_public_hostname_passes(monkeypatch):
    async def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(
        "asyncio.get_running_loop",
        lambda: type("L", (), {"getaddrinfo": staticmethod(fake_getaddrinfo)})(),
    )
    await svc.assert_fetchable("https://arxiv.org/pdf/2409.03245")


async def test_a_name_that_does_not_resolve_is_refused(monkeypatch):
    async def fake_getaddrinfo(host, port, *args, **kwargs):
        raise OSError("nxdomain")

    monkeypatch.setattr(
        "asyncio.get_running_loop",
        lambda: type("L", (), {"getaddrinfo": staticmethod(fake_getaddrinfo)})(),
    )
    with pytest.raises(svc.UnsafeUrl):
        await svc.assert_fetchable("http://nope.invalid/x.pdf")
