"""Extract paper title/abstract/body: Crossref (DOI), HTML meta tags, or LLM on PDF text."""

from __future__ import annotations

import html as _html_module
import re

import httpx
from pydantic import BaseModel

from app.core import debug_log
from app.core.logging import log


class _PaperMeta(BaseModel):
    title: str | None = None
    abstract: str | None = None


_DOI_RE = re.compile(r"10\.\d{4,}/[^\s\"<>]+")
_ARXIV_PDF_RE = re.compile(r"^(https?://arxiv\.org)/pdf/(.+?)(?:\.pdf)?$", re.IGNORECASE)
_CROSSREF_TIMEOUT = httpx.Timeout(8.0)

# Sent to academic APIs (Crossref, Semantic Scholar, Unpaywall)
_API_HEADERS = {"User-Agent": "ResearcherX/1.0 (mailto:researcherx@example.com)"}

# Sent when fetching publisher web pages — mimics a real browser to avoid WAF 403s
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}

# Keep the old name so paper_fetch_service.py still works unchanged
_UA = _API_HEADERS

_META_SYSTEM = (
    "You are a research paper metadata extractor. "
    "From the provided text (typically from the first page of a paper), extract the title and abstract. "
    "Return null for any field you cannot determine with confidence."
)


def extract_doi(url: str) -> str | None:
    m = _DOI_RE.search(url)
    return m.group(0) if m else None


def normalize_pdf_url(url: str) -> str:
    """Convert known PDF-view URLs to their HTML metadata equivalents.

    arXiv pdf URLs have a citation_title meta tag on the abs page — no LLM needed.
    """
    m = _ARXIV_PDF_RE.match(url)
    if m:
        return f"{m.group(1)}/abs/{m.group(2)}"
    return url


async def extract_title_from_doi(doi: str) -> str | None:
    """Query Crossref for the canonical title of a DOI."""
    debug_log.step("extract_title_from_doi", doi=doi)
    try:
        async with httpx.AsyncClient(timeout=_CROSSREF_TIMEOUT, headers=_UA) as client:
            r = await client.get(f"https://api.crossref.org/works/{doi}")
            debug_log.step("crossref_response", doi=doi, status=r.status_code)
            if r.status_code == 200:
                titles = r.json().get("message", {}).get("title", [])
                if titles and titles[0].strip():
                    result = titles[0].strip()
                    debug_log.step("crossref_title_found", title=result)
                    return result
                debug_log.step("crossref_no_title", doi=doi)
    except Exception as exc:
        log.debug("crossref_title_failed", doi=doi, error=str(exc))
        debug_log.step("crossref_error", doi=doi, error=str(exc))
    return None


async def _extract_meta_via_llm(text: str) -> _PaperMeta:
    """Extract title + abstract from the first ~3000 chars of PDF text via structured LLM call."""
    snippet = text[:3000].strip()
    if not snippet:
        return _PaperMeta()
    try:
        from app.llm.structured import parse_structured

        return await parse_structured(
            system=_META_SYSTEM,
            user=f"Text:\n{snippet}",
            output_model=_PaperMeta,
            max_tokens=600,
        )
    except Exception as exc:
        log.debug("llm_meta_extraction_failed", error=str(exc))
    return _PaperMeta()


def _meta_content(html: str, attr: str, val: str) -> str | None:
    """Extract content= from a <meta> tag regardless of attribute order or quoting.

    Handles both quoted (`property="og:title"`) and unquoted (`property=og:title`) attrs.
    """
    v = re.escape(val)
    q = r'["\']?'  # optional quote — some publishers (ScienceDirect) omit them
    for pattern in (
        # attr=val ... content="..."
        rf"<meta\b[^>]+\b{attr}={q}" + v + q + r'[^>]+\bcontent=["\']([^"\'<>]+)["\']',
        # content="..." ... attr=val
        r'<meta\b[^>]+\bcontent=["\']([^"\'<>]+)["\'][^>]+\b' + attr + r"=" + q + v + q,
    ):
        m = re.search(pattern, html, re.IGNORECASE)
        if m:
            return _html_module.unescape(m.group(1).strip())
    return None


_PUBLISHER_SUFFIX = re.compile(
    r"\s*[\|\-–]\s*(?:ScienceDirect|Elsevier|IEEE\s*Xplore|ACM\s*Digital\s*Library"
    r"|Springer(?:Link)?|Nature|Wiley|Taylor\s*&\s*Francis|MDPI|PubMed"
    r"|arXiv|ResearchGate|Semantic\s*Scholar).*$",
    re.IGNORECASE,
)


def _extract_title_from_html(snippet: str) -> str | None:
    citation = _meta_content(snippet, "name", "citation_title")
    debug_log.step("citation_title_meta", matched=citation is not None, value=citation)
    if citation:
        return citation[:300]

    og = _meta_content(snippet, "property", "og:title")
    debug_log.step("og_title_meta", matched=og is not None, value=og)
    if og:
        return og[:300]

    m = re.search(r"<title[^>]*>([^<]+)</title>", snippet, re.IGNORECASE)
    if m:
        title = _html_module.unescape(m.group(1).strip())
        title = _PUBLISHER_SUFFIX.sub("", title).strip()
        debug_log.step("title_tag", raw=m.group(1).strip(), cleaned=title)
        if title:
            return title[:300]
    debug_log.step("title_tag", matched=False)
    return None


def _extract_abstract_from_html(snippet: str) -> str | None:
    # citation_abstract (Google Scholar schema — arXiv, many publishers)
    ab = _meta_content(snippet, "name", "citation_abstract")
    if ab:
        return ab[:4000]
    # og:description / plain description
    ab = _meta_content(snippet, "property", "og:description")
    if ab:
        return ab[:4000]
    ab = _meta_content(snippet, "name", "description")
    if ab:
        return ab[:4000]
    return None


async def extract_meta_from_page(url: str) -> tuple[str | None, str | None]:
    """Fetch a URL and return (title, abstract).

    Handles HTML meta tags and direct PDF URLs (via fitz + LLM).
    arXiv PDF URLs are normalized to their abs page first.
    """
    normalized = normalize_pdf_url(url)
    if normalized != url:
        debug_log.step("url_normalized", original=url, normalized=normalized)
        url = normalized

    debug_log.step("extract_meta_from_page", url=url)
    try:
        async with httpx.AsyncClient(
            timeout=_CROSSREF_TIMEOUT, headers=_BROWSER_HEADERS, follow_redirects=True
        ) as client:
            async with client.stream("GET", url) as r:
                debug_log.step("page_http_response", url=url, status=r.status_code)
                if r.status_code != 200:
                    log.debug("page_meta_fetch_failed", url=url, status=r.status_code)
                    return None, None

                ct = r.headers.get("content-type", "")
                if "pdf" in ct.lower():
                    debug_log.step("pdf_content_detected", content_type=ct)
                    pdf_bytes = await r.aread()
                    meta = await extract_meta_from_pdf(pdf_bytes)
                    return meta[0], meta[1]

                chunks: list[str] = []
                total = 0
                async for chunk in r.aiter_text():
                    chunks.append(chunk)
                    total += len(chunk)
                    if "</head>" in chunk.lower() or total >= 200_000:
                        break
        snippet = "".join(chunks)
        debug_log.step(
            "page_bytes_read", bytes=total, found_head_close="</head>" in snippet.lower()
        )
        return _extract_title_from_html(snippet), _extract_abstract_from_html(snippet)
    except Exception as exc:
        log.debug("page_meta_extraction_failed", url=url, error=str(exc))
        debug_log.step("page_meta_error", error=str(exc))
    return None, None


async def extract_title_from_page(url: str) -> str | None:
    """Backward-compat wrapper — returns only the title."""
    title, _ = await extract_meta_from_page(url)
    return title


async def extract_meta_from_pdf(
    pdf_bytes: bytes,
) -> tuple[str | None, str | None, str | None]:
    """Extract (title, abstract, body) from a PDF via pymupdf4llm + structured LLM.

    Text-only markdown is stored as body — images are not embedded (see
    `paper_ingest_service._extract_markdown`); figure captions survive.
    """
    try:
        import fitz
        import pymupdf4llm

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        body_md = pymupdf4llm.to_markdown(doc)
        doc.close()
        body = body_md.strip() or None
        meta = await _extract_meta_via_llm(body_md)
        return meta.title, meta.abstract, body
    except Exception as exc:
        log.debug("pdf_meta_extraction_failed", error=str(exc))
    return None, None, None


async def extract_title_from_pdf(pdf_bytes: bytes) -> str | None:
    """Backward-compat wrapper — returns only the title."""
    title, _, _ = await extract_meta_from_pdf(pdf_bytes)
    return title
