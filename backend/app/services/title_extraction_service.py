"""Extract paper titles from PDFs via Crossref (DOI) or LLM fallback."""

from __future__ import annotations

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
