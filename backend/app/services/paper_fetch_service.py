# backend/app/services/paper_fetch_service.py
"""Fetch PDF bytes from a URL, with Unpaywall and Semantic Scholar OA fallback."""

from __future__ import annotations

import asyncio
import ipaddress
import re
from urllib.parse import urlparse

import httpx

from app.core.config import settings

_DOI_RE = re.compile(r"10\.\d{4,}/[^\s\"<>]+")
_TIMEOUT = httpx.Timeout(10.0)
_UA = {"User-Agent": "ResearcherX/1.0 (mailto:researcherx@example.com)"}
# Followed by hand rather than by httpx, so every hop is checked -- see
# `_get_pdf_bytes`.
_MAX_REDIRECTS = 5


class PaywallError(Exception):
    """Raised when a PDF cannot be fetched from any accessible source."""


class UnsafeUrl(Exception):
    """The URL names something inside our own network, or is not http(s)."""


def _is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Everything that is not a public internet address.

    A paper lives on the public web. Anything else this server can reach --
    loopback, the container network, the cloud metadata endpoint at
    169.254.169.254 -- is infrastructure, and fetching it on behalf of a user
    is SSRF whatever the response is then used for.
    """
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


async def assert_fetchable(url: str) -> None:
    """Refuse a URL that is not public http(s). Raises `UnsafeUrl`.

    Checked AFTER DNS resolution, and against every address the name
    resolves to: a hostname an attacker controls can simply resolve to
    127.0.0.1, so validating the hostname alone protects nothing.

    RESIDUAL RISK, deliberately not closed here: httpx resolves the name
    again when it connects, so a DNS entry that changes between the two
    lookups (rebinding) can still slip through. Closing it means connecting
    to the pinned address with a Host header, which is a custom transport --
    a bigger change than this guard, and worth doing only if this fetch ever
    starts handling anything more sensitive than an open-access PDF.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeUrl(f"{parsed.scheme or url!r} is not an http(s) URL")
    host = parsed.hostname
    if not host:
        raise UnsafeUrl("no host in URL")

    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, parsed.port or 0)
    except OSError as exc:
        raise UnsafeUrl(f"{host} does not resolve") from exc

    for info in infos:
        address = info[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            raise UnsafeUrl(f"{host} resolved to an unusable address") from None
        if _is_blocked(ip):
            raise UnsafeUrl(f"{host} resolves to a non-public address")


def _extract_doi(url: str) -> str | None:
    m = _DOI_RE.search(url)
    return m.group(0) if m else None


async def _get_pdf_bytes(url: str) -> bytes | None:
    """Fetch `url`, following redirects BY HAND so each hop is checked.

    `follow_redirects=True` would let a public URL bounce the request into
    our own network on the second hop, which is the same SSRF the guard on
    the first hop exists to stop.

    The body is read against a running counter rather than as `r.content`:
    the response is attacker-influenced and unbounded otherwise, and the
    extracted text from it is persisted even when the PDF itself is not.
    """
    cap = settings.paper_pdf_max_bytes
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as client:
            current = url
            for _ in range(_MAX_REDIRECTS + 1):
                await assert_fetchable(current)
                async with client.stream("GET", current, headers=_UA) as r:
                    if r.is_redirect:
                        location = r.headers.get("location")
                        if not location:
                            return None
                        current = str(httpx.URL(current).join(location))
                        continue
                    if r.status_code != 200:
                        return None
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in r.aiter_bytes():
                        total += len(chunk)
                        if total > cap:
                            return None
                        chunks.append(chunk)
                    body = b"".join(chunks)
                    return body if body[:4] == b"%PDF" else None
    except UnsafeUrl:
        # Re-raised, never swallowed into "no PDF here": a refused URL is a
        # different answer from a paywalled one and the caller says so.
        raise
    except Exception:
        pass
    return None


async def _resolve_oa_url(doi: str) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            # Unpaywall
            r = await client.get(
                f"https://api.unpaywall.org/v2/{doi}",
                params={"email": "researcherx@example.com"},
                headers=_UA,
            )
            if r.status_code == 200:
                loc = r.json().get("best_oa_location") or {}
                oa_url = loc.get("url_for_pdf")
                if oa_url:
                    return oa_url

            # Semantic Scholar
            r = await client.get(
                f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}",
                params={"fields": "openAccessPdf"},
                headers=_UA,
            )
            if r.status_code == 200:
                oa = r.json().get("openAccessPdf") or {}
                oa_url = oa.get("url")
                if oa_url:
                    return oa_url
    except Exception:
        pass
    return None


async def fetch_pdf(url: str) -> tuple[bytes, str]:
    """Return `(pdf_bytes, url_that_served_them)`.

    The second element is what makes an open-access fallback visible to the
    caller: when the pasted URL 403s and Unpaywall or Semantic Scholar
    supplies a mirror, ingestion succeeds against a DIFFERENT url, and a
    caller that recorded only what the user typed would later send the
    reader to the paywall this function just worked around.

    Raises `PaywallError` if no accessible source is found, and `UnsafeUrl`
    if the URL names something inside our own network.
    """
    pdf = await _get_pdf_bytes(url)
    if pdf:
        return pdf, url

    doi = _extract_doi(url)
    if doi:
        oa_url = await _resolve_oa_url(doi)
        if oa_url:
            pdf = await _get_pdf_bytes(oa_url)
            if pdf:
                return pdf, oa_url

    raise PaywallError(url)
