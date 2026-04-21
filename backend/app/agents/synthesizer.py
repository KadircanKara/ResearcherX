from collections.abc import AsyncIterator

from pydantic import BaseModel

from app.core.config import settings
from app.llm.client import get_client
from app.schemas.research import SearchFinding

SYSTEM = (
    "You are a research synthesizer. Given a user question and a set of focused findings, "
    "write a well-structured Markdown report that answers the question. Cite sources inline "
    "using [domain](url) form. Surface disagreements between findings explicitly. Do not invent "
    "facts beyond the findings."
)


class SynthesizerInput(BaseModel):
    question: str
    findings: list[SearchFinding]


class SynthesizerAgent:
    name = "synthesizer"

    async def stream(self, inp: SynthesizerInput) -> AsyncIterator[str]:
        findings_text = "\n\n".join(
            f"### Sub-query: {f.query}\n{f.summary}\nSources: {', '.join(f.sources)}"
            for f in inp.findings
        )
        client = get_client()
        stream = await client.chat.completions.create(
            model=settings.llm_model,
            max_tokens=4000,
            stream=True,
            messages=[
                {"role": "system", "content": SYSTEM},
                {
                    "role": "user",
                    "content": f"Question: {inp.question}\n\nFindings:\n{findings_text}",
                },
            ],
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
