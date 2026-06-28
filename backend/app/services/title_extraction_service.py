"""Extract paper titles: Crossref (DOI), HTML meta tags, or LLM on PDF text."""

from __future__ import annotations

import html as _html_module
import re

import httpx

from app.core import debug_log
from app.core.logging import log

_DOI_RE = re.compile(r"10\.\d{4,}/[^\s\"<>]+")
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

_SYSTEM_PROMPT = (
    "You are a research paper title extractor. "
    "Extract only the title of the paper from the text provided. "
    "Return ONLY the title — no quotes, no explanation, no punctuation other than what is in the title itself. "
    "If you cannot determine the title, respond with exactly: UNKNOWN"
)


def extract_doi(url: str) -> str | None:
    m = _DOI_RE.search(url)
    return m.group(0) if m else None


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


async def extract_title_via_llm(text: str) -> str | None:
    """Ask the LLM to extract the title from the first ~1500 chars of PDF text."""
    snippet = text[:1500].strip()
    if not snippet:
        return None
    try:
        from app.llm.client import create_chat_completion

        resp = await create_chat_completion(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"Text:\n{snippet}"},
            ],
            max_tokens=120,
        )
        title = resp.choices[0].message.content.strip()
        if title and title != "UNKNOWN":
            return title[:300]
    except Exception as exc:
        log.debug("llm_title_extraction_failed", error=str(exc))
    return None


def _meta_content(html: str, attr: str, val: str) -> str | None:
    """Extract content= from a <meta> tag regardless of attribute order or quoting.

    Handles both quoted (`property="og:title"`) and unquoted (`property=og:title`) attrs.
    """
    v = re.escape(val)
    q = r'["\']?'  # optional quote — some publishers (ScienceDirect) omit them
    for pattern in (
        # attr=val ... content="..."
        rf'<meta\b[^>]+\b{attr}={q}' + v + q + r'[^>]+\bcontent=["\']([^"\'<>]+)["\']',
        # content="..." ... attr=val
        r'<meta\b[^>]+\bcontent=["\']([^"\'<>]+)["\'][^>]+\b' + attr + r'=' + q + v + q,
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



async def extract_title_from_page(url: str) -> str | None:
    """Fetch the web page and extract title from academic meta tags.

    Priority:
    1. <meta name="citation_title"> — Google Scholar standard; all major publishers
    2. <meta property="og:title">
    3. <title> tag with common publisher suffixes stripped

    Uses browser-like headers to avoid WAF 403s on publisher sites.
    """
    debug_log.step("extract_title_from_page", url=url)
    try:
        async with httpx.AsyncClient(
            timeout=_CROSSREF_TIMEOUT, headers=_BROWSER_HEADERS, follow_redirects=True
        ) as client:
            async with client.stream("GET", url) as r:
                debug_log.step("page_http_response", url=url, status=r.status_code)
                if r.status_code != 200:
                    log.debug("page_title_fetch_failed", url=url, status=r.status_code)
                    return None
                chunks: list[str] = []
                total = 0
                async for chunk in r.aiter_text():
                    chunks.append(chunk)
                    total += len(chunk)
                    if "</head>" in chunk.lower() or total >= 200_000:
                        break
        snippet = "".join(chunks)
        debug_log.step("page_bytes_read", bytes=total, found_head_close="</head>" in snippet.lower())

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
        else:
            debug_log.step("title_tag", matched=False)
    except Exception as exc:
        log.debug("page_title_extraction_failed", url=url, error=str(exc))
        debug_log.step("page_title_error", error=str(exc))
    return None


async def extract_title_from_pdf(pdf_bytes: bytes) -> str | None:
    """Extract text from the first page of a PDF, then ask the LLM for the title."""
    try:
        import fitz

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        first_page_text = doc[0].get_text("text", sort=True) if len(doc) > 0 else ""
        doc.close()
        return await extract_title_via_llm(first_page_text)
    except Exception as exc:
        log.debug("pdf_title_extraction_failed", error=str(exc))
    return None
