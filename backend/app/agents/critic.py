from pydantic import BaseModel

from app.llm.structured import parse_structured
from app.schemas.research import Critique, SearchFinding

SYSTEM = (
    "You are a research critic. Given a draft report and the findings it was built from, "
    "flag claims that are not supported by the findings, missing citations, and internal "
    "contradictions. Be concise; only raise real issues. If the report is solid, return an "
    "empty issues list and overall=pass."
)


class CriticInput(BaseModel):
    question: str
    draft_report: str
    findings: list[SearchFinding]


class CriticAgent:
    name = "critic"

    async def run(self, inp: CriticInput) -> Critique:
        findings_text = "\n\n".join(
            f"### {f.query}\n{f.summary}\nSources: {', '.join(f.sources)}" for f in inp.findings
        )
        return await parse_structured(
            system=SYSTEM,
            user=(
                f"Question: {inp.question}\n\n"
                f"Draft report:\n{inp.draft_report}\n\n"
                f"Findings used:\n{findings_text}"
            ),
            output_model=Critique,
            max_tokens=3000,
        )
