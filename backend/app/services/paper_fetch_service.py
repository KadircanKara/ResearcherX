# backend/app/services/paper_fetch_service.py
"""Fetch PDF bytes from a URL, with Unpaywall and Semantic Scholar OA fallback."""
from __future__ import annotations

import re

import httpx

_DOI_RE = re.compile(r"10\.\d{4,}/[^\s\"<>]+")
_TIMEOUT = httpx.Timeout(10.0)
_UA = {"User-Agent": "ResearcherX/1.0 (mailto:researcherx@example.com)"}


class PaywallError(Exception):
    """Raised when a PDF cannot be fetched from any accessible source."""


def _extract_doi(url: str) -> str | None:
    m = _DOI_RE.search(url)
    return m.group(0) if m else None


async def _get_pdf_bytes(url: str) -> bytes | None:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            r = await client.get(url, headers=_UA)
            if r.status_code == 200 and r.content[:4] == b"%PDF":
                return r.content
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


async def fetch_pdf(url: str) -> bytes:
    """Return PDF bytes for url, trying Unpaywall + Semantic Scholar on 403/non-PDF.

    Raises PaywallError if no accessible source is found.
    """
    pdf = await _get_pdf_bytes(url)
    if pdf:
        return pdf

    doi = _extract_doi(url)
    if doi:
        oa_url = await _resolve_oa_url(doi)
        if oa_url:
            pdf = await _get_pdf_bytes(oa_url)
            if pdf:
                return pdf

    raise PaywallError(url)
