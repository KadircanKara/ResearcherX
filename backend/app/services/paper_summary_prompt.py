"""Prompt, schema, parser and keyword clean-up for paper summaries.

PURE: stdlib only, no DB, no ORM, no stateful services. `app.db.models` imports
`SUMMARY_VER` from here, so this module must never import `app.db`.
"""

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass

SUMMARY_VER = 1

SYSTEM = """You write index records for academic papers in a research search engine. Each record is used to decide which papers might answer a researcher's question, so coverage of what the paper actually contains matters more than elegance.

Return:
- summary: 3-5 sentences, plain text. Cover the problem, the method, the data or setting (datasets, simulators, platforms), the main findings with any headline numbers, and the stated limitations if the paper gives them.
- keywords: the paper's author keywords, then every method, model, algorithm, dataset, benchmark, simulator, metric and task the paper uses or proposes. Write each exactly as the paper writes it. When the paper defines an acronym, list the acronym and its expansion as two separate keywords. Only include terms that appear in the paper."""

SCHEMA: dict = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "keywords": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "keywords"],
    "additionalProperties": False,
}

_NUMBER_ONLY = re.compile(
    r"^[\d.,eE+\-−×^ ]+\s*(%|m|s|ms|m/s|km|Hz|GHz|MHz|dB|dBm|kg|W|Gbps|Mbps)?$"
)
_DEDUPE_SEP = re.compile(r"[\s\-‐–—_]+")


@dataclass(frozen=True)
class SummaryRecord:
    summary: str
    keywords: tuple[str, ...]


def full_text(
    extracted_pages: list | None, body: str | None, abstract: str | None, *, cap: int
) -> str:
    """Page texts in page order, else the body, else the abstract; capped."""
    text = ""
    if extracted_pages:
        pages = sorted(
            (
                p
                for p in extracted_pages
                if isinstance(p, dict) and isinstance(p.get("text"), str) and p["text"]
            ),
            key=lambda p: ((0, p["page"]) if isinstance(p.get("page"), int) else (1, 0)),
        )
        text = "\n\n".join(p["text"] for p in pages)
    if not text:
        text = body or abstract or ""
    return text[:cap]


def messages(title: str, abstract: str | None, text: str) -> list[dict]:
    user = f"Title: {title}\n\nAbstract: {abstract or ''}\n\nFull text:\n{text}"
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def response_format() -> dict:
    return {
        "type": "json_schema",
        "json_schema": {"name": "paper_record", "strict": True, "schema": SCHEMA},
    }


def clean_keywords(raw: Iterable[str], *, max_keywords: int) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        k = item.strip()
        if not k or _NUMBER_ONLY.fullmatch(k):
            continue
        key = _DEDUPE_SEP.sub(" ", k.lower()).strip()
        if key in seen:
            continue
        seen.add(key)
        out.append(k)
        if len(out) >= max_keywords:
            break
    return tuple(out)


def parse(content: str | None, *, max_keywords: int) -> SummaryRecord | None:
    """None on invalid JSON, a schema mismatch or a blank summary."""
    if not content:
        return None
    try:
        data = json.loads(content)
    except (ValueError, RecursionError):
        return None
    if not isinstance(data, dict) or set(data) != {"summary", "keywords"}:
        return None
    summary, keywords = data["summary"], data["keywords"]
    if not isinstance(summary, str) or not summary.strip():
        return None
    if not isinstance(keywords, list) or not all(isinstance(k, str) for k in keywords):
        return None
    return SummaryRecord(
        summary=summary.strip(),
        keywords=clean_keywords(keywords, max_keywords=max_keywords),
    )


def embed_text(title: str, abstract: str | None, summary: str) -> str:
    return f"{title}\n\n{abstract or ''}\n\n{summary}"
