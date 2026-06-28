"""Extract paper titles: Crossref (DOI), HTML meta tags, or LLM on PDF text."""

from __future__ import annotations

import html as _html_module
import re

import httpx

from app.core.logging import log

_DOI_RE = re.compile(r"10\.\d{4,}/[^\s\"<>]+")
_CROSSREF_TIMEOUT = httpx.Timeout(8.0)
_UA = {"User-Agent": "ResearcherX/1.0 (mailto:researcherx@example.com)"}

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
    try:
        async with httpx.AsyncClient(timeout=_CROSSREF_TIMEOUT, headers=_UA) as client:
            r = await client.get(f"https://api.crossref.org/works/{doi}")
            if r.status_code == 200:
                titles = r.json().get("message", {}).get("title", [])
                if titles and titles[0].strip():
                    return titles[0].strip()
    except Exception as exc:
        log.debug("crossref_title_failed", doi=doi, error=str(exc))
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
    """Extract content= from <meta {attr}="{val}" content="..."> regardless of attr order."""
    v = re.escape(val)
    for pattern in (
        rf'<meta\b[^>]+\b{attr}=["\']' + v + r'["\'][^>]+\bcontent=["\']([^"\'<>]+)["\']',
        r'<meta\b[^>]+\bcontent=["\']([^"\'<>]+)["\'][^>]+\b' + attr + r'=["\']' + v + r'["\']',
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

    Only reads the first 40 KB (head section always comes first).
    """
    try:
        async with httpx.AsyncClient(
            timeout=_CROSSREF_TIMEOUT, headers=_UA, follow_redirects=True
        ) as client:
            r = await client.get(url)
            if r.status_code != 200:
                log.debug("page_title_fetch_failed", url=url, status=r.status_code)
                return None
            snippet = r.text[:40_000]

        title = (
            _meta_content(snippet, "name", "citation_title")
            or _meta_content(snippet, "property", "og:title")
        )
        if title:
            return title[:300]

        # Fallback: <title> tag, strip " - ScienceDirect" style suffixes
        m = re.search(r"<title[^>]*>([^<]+)</title>", snippet, re.IGNORECASE)
        if m:
            title = _html_module.unescape(m.group(1).strip())
            title = _PUBLISHER_SUFFIX.sub("", title).strip()
            if title:
                return title[:300]
    except Exception as exc:
        log.debug("page_title_extraction_failed", url=url, error=str(exc))
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
