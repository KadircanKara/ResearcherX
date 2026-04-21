from pydantic import BaseModel

from app.llm.structured import parse_structured
from app.schemas.research import ResearchPlan

SYSTEM = (
    "You are a research planner. Given a question, decompose it into 3 focused sub-queries "
    "suitable for web search. Each sub-query should cover a distinct angle and be phrased as "
    "a search string, not a question. Keep the rationale to one or two sentences."
)


class PlannerInput(BaseModel):
    question: str


class PlannerAgent:
    name = "planner"

    async def run(self, inp: PlannerInput) -> ResearchPlan:
        return await parse_structured(
            system=SYSTEM,
            user=inp.question,
            output_model=ResearchPlan,
            max_tokens=2000,
        )
