from pydantic import BaseModel

from app.llm.structured import parse_structured
from app.schemas.research import Critique, NumberedSource

SYSTEM = (
    "You are a research critic. You are given a draft report and the NUMBERED SOURCE CATALOG "
    "it was built from. Flag only real issues:\n"
    "- claims not supported by the catalog;\n"
    "- internal contradictions;\n"
    "- CITATION INTEGRITY: a claim whose inline [n] points to a catalog source that does not "
    "actually support it (number↔claim mismatch); a [n] in the body that is not in the "
    "catalog; a reference listed in '## References' that is never cited in the body; or a "
    "number cited in the body but missing from References.\n"
    "Be concise; only raise real issues. If the report is solid, return an empty issues list "
    "and overall=pass."
)


class CriticInput(BaseModel):
    question: str
    draft_report: str
    sources: list[NumberedSource]


class CriticAgent:
    name = "critic"

    async def run(self, inp: CriticInput) -> Critique:
        catalog = "\n".join(f"[{s.n}] {s.url} — {s.summary}" for s in inp.sources)
        return await parse_structured(
            system=SYSTEM,
            user=(
                f"Question: {inp.question}\n\n"
                f"Draft report:\n{inp.draft_report}\n\n"
                f"NUMBERED SOURCE CATALOG:\n{catalog}"
            ),
            output_model=Critique,
            max_tokens=3000,
        )
