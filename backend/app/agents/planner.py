from pydantic import BaseModel

from app.llm.structured import parse_structured
from app.schemas.research import FindingValidation, ResearchPlan, SearchFinding

SYSTEM = (
    "You are a research planner. Given a question, decompose it into 3 focused sub-queries "
    "suitable for web search. Each sub-query should cover a distinct angle and be phrased as "
    "a search string, not a question. Keep the rationale to one or two sentences."
)

VALIDATE_SYSTEM = (
    "You are a research planner reviewing a web-search finding produced for one of your "
    "sub-queries. Judge it against the ORIGINAL question: Is it on-topic? Is it complete "
    "and useful? Is it non-empty? If it is usable downstream, respond verdict='valid'. "
    "If not (off-topic, empty, or unhelpful), respond verdict='invalid', give brief "
    "reasons, and propose one revised_query that would get better results. The revised "
    "query must be a search string, not a question, and must differ meaningfully from "
    "the one that was searched."
)


class PlannerInput(BaseModel):
    question: str


class ValidationInput(BaseModel):
    question: str  # the original user question
    sub_query: str  # the query that was actually searched
    finding: SearchFinding


class PlannerAgent:
    name = "planner"

    async def run(self, inp: PlannerInput) -> ResearchPlan:
        return await parse_structured(
            system=SYSTEM,
            user=inp.question,
            output_model=ResearchPlan,
            max_tokens=2000,
        )

    async def validate(self, inp: ValidationInput) -> FindingValidation:
        """Judge a searcher finding and, if invalid, propose the revised query.

        The planner does not pass garbage downstream: the orchestrator retries
        invalid findings with the revised query (capped) before synthesis.
        """
        user = (
            f"Original question: {inp.question}\n"
            f"Sub-query searched: {inp.sub_query}\n\n"
            f"Finding summary:\n{inp.finding.summary}\n\n"
            f"Sources ({len(inp.finding.sources)}): "
            f"{', '.join(inp.finding.sources) or '(none)'}"
        )
        return await parse_structured(
            system=VALIDATE_SYSTEM,
            user=user,
            output_model=FindingValidation,
            max_tokens=400,  # small: verdict + brief reasons + one query
        )
