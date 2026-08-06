"""Extract paper title/abstract/body: Crossref (DOI), HTML meta tags, or LLM on PDF text."""

from __future__ import annotations

import html as _html_module
import re

import httpx
from pydantic import BaseModel, Field, field_validator

from app.core import debug_log
from app.core.logging import log
from app.llm.structured import parse_structured
from app.services.text_normalization import recompose_diacritics

_MAX_AUTHORS = 50
_MAX_AUTHOR_LEN = 200
_MAX_VENUE_LEN = 300
_MIN_YEAR = 1500
_MAX_YEAR = 2100


class PaperMeta(BaseModel):
    """Structured metadata for one paper. Every field may legitimately be absent.

    The validators are `mode="before"` because this model is built straight from
    LLM JSON: a string where a list was asked for, a year as `"2024-05"`, or a
    blank venue are all normal outputs from a noisy endpoint, and each must
    degrade to a clean value or to absence rather than raise mid-ingest.
    """

    title: str | None = None
    abstract: str | None = None
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str | None = None

    @field_validator("authors", mode="before")
    @classmethod
    def _coerce_authors(cls, v: object) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            v = v.split(",")
        if not isinstance(v, list):
            return []
        out: list[str] = []
        for item in v:
            if not isinstance(item, str):
                continue
            name = recompose_diacritics(" ".join(item.split()))
            if name:
                out.append(name[:_MAX_AUTHOR_LEN])
        return out[:_MAX_AUTHORS]

    @field_validator("year", mode="before")
    @classmethod
    def _coerce_year(cls, v: object) -> int | None:
        if v is None:
            return None
        try:
            year = int(str(v).strip()[:4])
        except (TypeError, ValueError):
            return None
        return year if _MIN_YEAR <= year <= _MAX_YEAR else None

    @field_validator("venue", mode="before")
    @classmethod
    def _coerce_venue(cls, v: object) -> str | None:
        if not isinstance(v, str):
            return None
        return recompose_diacritics(" ".join(v.split()))[:_MAX_VENUE_LEN] or None


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

# Absence is the common case, not the exception: both papers in the dev corpus
# are preprints with no stated year and no venue. A prompt that implies every
# field should be filled produces invented ones, which is the same fabrication
# harm as an answer that declines and then speculates.
_META_SYSTEM = (
    "You are a research paper metadata extractor. "
    "From the provided text (typically the first page of a paper), extract "
    "title, abstract, authors, year, and venue.\n"
    "- authors: a JSON array of the full display name of each author, in the "
    "order printed on the paper. Return each name exactly as it appears, even "
    "if PDF extraction mangled its accents — never substitute a different "
    "spelling or a different name. Exclude affiliations, departments, "
    "universities, cities, and email addresses. Return [] if no author names "
    "are present.\n"
    "- year: the publication year as a 4-digit integer, and only if the paper "
    "itself states it. Return null if it is absent — never infer it from a "
    "copyright line, a cited reference, or a file date.\n"
    "- venue: the conference or journal the paper was published in, and only "
    "if the paper itself names it. Return null if absent.\n"
    "Absence is a correct answer. Most preprints state neither a year nor a "
    "venue. A field you invent is worse than a field you leave null."
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


_CROSSREF_DATE_KEYS = ("published", "published-print", "published-online", "issued")


def _first_string(values: object) -> str | None:
    """First non-blank string of a Crossref list field. Blank is absence.

    Crossref returns HTML-escaped strings (e.g. "... Discovery &amp; Data Mining").
    Unescape MUST run before strip: an entity like `&nbsp;` decodes to a
    non-breaking space rather than to nothing, so only a strip that runs after
    unescaping will trim it. Stripping first would leave that padding in place.
    """
    if not isinstance(values, list) or not values:
        return None
    first = values[0]
    if not isinstance(first, str):
        return None
    return _html_module.unescape(first).strip() or None


def parse_crossref_message(message: dict) -> PaperMeta:
    """Map a Crossref `message` object onto PaperMeta.

    Every field is guarded independently: Crossref returns keys present-but-null
    for records that lack them, and `date-parts` can be `[[None]]`.
    """
    authors: list[str] = []
    for entry in message.get("author") or []:
        if not isinstance(entry, dict):
            continue
        # Crossref splits person names into given/family; join them into the
        # same full-display-name shape the LLM path produces, so the two
        # sources are comparable and the evaluation can score both.
        parts = [
            p.strip()
            for p in (entry.get("given"), entry.get("family"))
            if isinstance(p, str) and p.strip()
        ]
        name = " ".join(parts)
        if not name:
            # Organisational authors carry `name` instead of given/family.
            org = entry.get("name")
            name = org.strip() if isinstance(org, str) else ""
        if name:
            authors.append(name)

    year: int | None = None
    for key in _CROSSREF_DATE_KEYS:
        parts = (message.get(key) or {}).get("date-parts") or []
        if parts and isinstance(parts[0], list) and parts[0] and isinstance(parts[0][0], int):
            year = parts[0][0]
            break

    return PaperMeta(
        title=_first_string(message.get("title")),
        authors=authors,
        year=year,
        venue=_first_string(message.get("container-title")),
    )


async def fetch_crossref_meta(doi: str) -> PaperMeta | None:
    """Query Crossref for a DOI's full record. None on any failure."""
    debug_log.step("fetch_crossref_meta", doi=doi)
    try:
        async with httpx.AsyncClient(timeout=_CROSSREF_TIMEOUT, headers=_UA) as client:
            r = await client.get(f"https://api.crossref.org/works/{doi}")
            debug_log.step("crossref_response", doi=doi, status=r.status_code)
            if r.status_code != 200:
                return None
            message = r.json().get("message")
            if not isinstance(message, dict):
                debug_log.step("crossref_no_message", doi=doi)
                return None
            meta = parse_crossref_message(message)
            debug_log.step(
                "crossref_meta_found", title=meta.title, authors=len(meta.authors), year=meta.year
            )
            return meta
    except Exception as exc:
        log.debug("crossref_meta_failed", doi=doi, error=str(exc))
        debug_log.step("crossref_error", doi=doi, error=str(exc))
    return None


async def extract_title_from_doi(doi: str) -> str | None:
    """Query Crossref for the canonical title of a DOI."""
    meta = await fetch_crossref_meta(doi)
    return meta.title if meta is not None else None


async def extract_metadata_from_text(text: str) -> PaperMeta:
    """Extract metadata from the first ~3000 chars of paper text via one LLM call.

    Fails open to an empty PaperMeta: metadata is an enhancement and must never
    block a paper from being ingested.
    """
    snippet = text[:3000].strip()
    if not snippet:
        return PaperMeta()
    try:
        return await parse_structured(
            system=_META_SYSTEM,
            user=f"Text:\n{snippet}",
            output_model=PaperMeta,
            max_tokens=800,
        )
    except Exception as exc:
        log.debug("llm_meta_extraction_failed", error=str(exc))
    return PaperMeta()


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
        meta = await extract_metadata_from_text(body_md)
        return meta.title, meta.abstract, body
    except Exception as exc:
        log.debug("pdf_meta_extraction_failed", error=str(exc))
    return None, None, None


async def extract_title_from_pdf(pdf_bytes: bytes) -> str | None:
    """Backward-compat wrapper — returns only the title."""
    title, _, _ = await extract_meta_from_pdf(pdf_bytes)
    return title
