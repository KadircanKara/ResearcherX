"""QueryReformulatorAgent — rewrites a follow-up into a standalone query.

Retrieval is a global top-k by cosine similarity, so nothing here allocates
chunks — and, deliberately, nothing here sees the paper list. This agent
exists for one measured reason: a follow-up like "And against the RL one?"
has no retrievable content of its own and embeds to noise. Its cost is
therefore O(1) in library size, unlike the retrieval planner it replaces,
whose prompt carried every paper's title and abstract (~13k tokens at 100
papers).

Fail-open: on any error, return the original query unchanged.
"""

from pydantic import BaseModel

from app.core.config import settings
from app.core.logging import log
from app.llm.structured import parse_structured

SYSTEM = (
    "Rewrite the user's latest question into a single standalone search query.\n\n"
    "Resolve pronouns and references using the prior conversation, and expand "
    "abbreviations. Keep the user's own wording wherever it is already clear — "
    "if the question already stands alone, copy it verbatim. Do NOT answer the "
    "question, do NOT add facts the conversation does not contain, and do NOT "
    "invent paper titles."
)


class ReformulatorInput(BaseModel):
    query: str
    prior_messages: list[dict]  # [{"role": "user"|"assistant", "content": "..."}]


class ReformulatedQuery(BaseModel):
    query: str = ""


class QueryReformulatorAgent:
    name = "query_reformulator"

    async def run(self, inp: ReformulatorInput) -> str:
        history = "\n".join(
            f"[{m['role'].upper()}]: {m['content'][:300]}"
            for m in inp.prior_messages[-6:]  # last 6 messages max
        )
        user = f"PRIOR CONVERSATION:\n{history}\n\nLATEST QUESTION: {inp.query}"
        try:
            out = await parse_structured(
                system=SYSTEM,
                user=user,
                output_model=ReformulatedQuery,
                max_tokens=settings.query_reformulator_max_tokens,
            )
        except Exception as exc:
            log.warning("query_reformulator_failed_open", error=str(exc)[:200])
            return inp.query
        # An empty rewrite embeds to a meaningless vector, so it is a failure
        # like any other — the original question is always usable.
        return out.query.strip() or inp.query
