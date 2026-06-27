"""RetrievalPlannerAgent — decides per-paper chunk allocation for a query.

Fail-open: on any error, returns mode=broad with 2 chunks per paper and
the original query unchanged. This keeps the chat pipeline robust against
flaky structured-output calls.
"""
from pydantic import BaseModel, Field

from app.core.logging import log
from app.llm.structured import parse_structured

SYSTEM = (
    "You are a retrieval planner for a project-scoped research assistant. "
    "Given a user query, relevant prior conversation, and a list of assigned papers, "
    "decide how many text chunks to retrieve from each paper.\n\n"
    "Modes:\n"
    "- 'comparative': user compares/contrasts across papers (words: compare, difference, "
    "all papers, each) → 3 chunks each, ALL papers\n"
    "- 'targeted': query references a specific paper or narrow concept → 5 chunks from "
    "that paper, 1 from all others (never 0)\n"
    "- 'broad': general/factual question → 2–3 chunks each, all papers\n\n"
    "Rules: never output 0 chunks for any paper; use prior conversation to resolve "
    "pronouns in the query; reformulated_query should expand abbreviations and "
    "resolve references for better vector search (copy if already clear)."
)


class PaperInfo(BaseModel):
    paper_id: str
    title: str
    abstract: str


class PlannerInput(BaseModel):
    query: str
    paper_list: list[PaperInfo | dict]
    prior_messages: list[dict]   # [{"role": "user"|"assistant", "content": "..."}]


class PaperAlloc(BaseModel):
    paper_id: str
    chunks: int = Field(ge=1, le=6)


class RetrievalPlan(BaseModel):
    mode: str = "broad"
    reformulated_query: str = ""
    per_paper: list[PaperAlloc] = Field(default_factory=list)


class RetrievalPlannerAgent:
    name = "retrieval_planner"

    async def run(self, inp: PlannerInput) -> RetrievalPlan:
        paper_list = [
            p if isinstance(p, PaperInfo) else PaperInfo(**p)
            for p in inp.paper_list
        ]
        paper_block = "\n".join(
            f"[{p.paper_id}] {p.title}\nAbstract: {(p.abstract or '')[:300]}"
            for p in paper_list
        )
        history_block = "\n".join(
            f"[{m['role'].upper()}]: {m['content'][:300]}"
            for m in inp.prior_messages[-6:]     # last 6 messages max
        ) or "(no prior conversation)"

        user = (
            f"USER QUERY: {inp.query}\n\n"
            f"RELEVANT PRIOR CONVERSATION:\n{history_block}\n\n"
            f"ASSIGNED PAPERS:\n{paper_block}"
        )
        try:
            return await parse_structured(
                system=SYSTEM,
                user=user,
                output_model=RetrievalPlan,
                max_tokens=600,
            )
        except Exception as exc:
            log.warning("retrieval_planner_failed_open", error=str(exc)[:200])
            # Fail open: broad retrieval, original query
            return RetrievalPlan(
                mode="broad",
                reformulated_query=inp.query,
                per_paper=[PaperAlloc(paper_id=p.paper_id, chunks=2) for p in paper_list],
            )
