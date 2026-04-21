from pydantic import BaseModel

from app.core.logging import log
from app.llm.structured import parse_structured
from app.schemas.research import SearchFinding
from app.tools.web_search import WebSearchInput, WebSearchTool

SYSTEM = (
    "You are a research searcher. You will receive raw web search hits for one sub-query. "
    "Synthesize them into a concise, factual summary (3–6 sentences) and list the source URLs "
    "you actually relied on. Do not speculate beyond the hits."
)


class SearcherInput(BaseModel):
    query: str


class SearcherAgent:
    name = "searcher"

    def __init__(self) -> None:
        self._search = WebSearchTool()

    async def run(self, inp: SearcherInput) -> SearchFinding:
        hits = await self._search(WebSearchInput(query=inp.query, max_results=5))
        if not hits.hits:
            return SearchFinding(query=inp.query, summary="No results found.", sources=[])

        hit_text = "\n".join(f"- [{h.title}]({h.url}) — {h.snippet}" for h in hits.hits)
        try:
            return await parse_structured(
                system=SYSTEM,
                user=f"Sub-query: {inp.query}\n\nHits:\n{hit_text}",
                output_model=SearchFinding,
                max_tokens=1500,
            )
        except Exception as exc:  # noqa: BLE001
            # One flaky free-model response shouldn't kill the whole run.
            # Fall back to raw hits so the synthesizer still has material.
            log.warning("searcher_degraded", query=inp.query, error=str(exc))
            summary = " ".join(h.snippet for h in hits.hits if h.snippet)[:800] or (
                f"Summarization failed; raw hits available for '{inp.query}'."
            )
            return SearchFinding(
                query=inp.query,
                summary=summary,
                sources=[h.url for h in hits.hits],
            )
