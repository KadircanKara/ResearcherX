import asyncio

from ddgs import DDGS
from pydantic import BaseModel

from app.core.logging import log


class WebSearchInput(BaseModel):
    query: str
    max_results: int = 5


class WebSearchHit(BaseModel):
    title: str
    url: str
    snippet: str


class WebSearchOutput(BaseModel):
    query: str
    hits: list[WebSearchHit]


class WebSearchTool:
    """Free DuckDuckGo search via ddgs. No API key required.

    Swap this file to integrate Tavily, Serper, or a self-hosted index — the
    tool interface (pydantic in/out) stays the same.
    """

    name = "web_search"

    async def __call__(self, inp: WebSearchInput) -> WebSearchOutput:
        try:
            raw = await asyncio.to_thread(self._search_sync, inp.query, inp.max_results)
        except Exception as exc:  # noqa: BLE001
            log.warning("web_search_failed", query=inp.query, error=str(exc))
            raw = []

        hits = [
            WebSearchHit(
                title=r.get("title", "").strip(),
                url=r.get("href", "").strip(),
                snippet=r.get("body", "").strip(),
            )
            for r in raw
            if r.get("href")
        ]
        return WebSearchOutput(query=inp.query, hits=hits)

    @staticmethod
    def _search_sync(query: str, max_results: int) -> list[dict]:
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results))
