from collections.abc import AsyncIterator

from pydantic import BaseModel

from app.llm.client import create_chat_completion
from app.schemas.research import SearchFinding

SYSTEM = (
    "You are a research synthesizer. Given a user question and a set of focused findings, "
    "write a well-structured GitHub-flavored Markdown report that answers the question. "
    "Cite claims inline with bracketed numbers like [1] referring to source URLs, and end "
    "the report with a '## References' section listing every cited number as '[n] url' on "
    "its own line — number sources in order of first citation and only list sources you "
    "actually cited. Surface disagreements between findings explicitly. Do not invent facts "
    "beyond the findings."
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
        # Failover-aware: only the initial request can 429 — the pool rotates
        # there; an already-started stream never needs rescuing.
        stream = await create_chat_completion(
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
