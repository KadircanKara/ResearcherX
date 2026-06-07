from collections.abc import AsyncIterator

from pydantic import BaseModel

from app.llm.client import create_chat_completion
from app.schemas.research import NumberedSource

SYSTEM = (
    "You are a research synthesizer. Given a user question, the sub-queries that were "
    "researched, and a NUMBERED SOURCE CATALOG, write a well-structured GitHub-flavored "
    "Markdown report that answers the question.\n\n"
    "CITATION RULES — follow exactly:\n"
    "- Cite every non-trivial claim inline with the bracketed catalog number of the source "
    "that supports it, e.g. [3]. A claim drawn from a source MUST carry THAT source's "
    "catalog number — never reassign or renumber.\n"
    "- Use ONLY numbers that appear in the catalog; never invent a number or cite a source "
    "that is not in the catalog.\n"
    "- Base claims on the catalog summaries; do not invent facts beyond them.\n"
    "- End with a '## References' section. List ONLY the sources you actually cited in the "
    "body, one per line as '[n] url', copying the number and url verbatim from the catalog, "
    "in ascending numeric order.\n"
    "- A source you did not cite in the body MUST NOT appear in References. A number that "
    "appears in References MUST also appear at least once in the body.\n\n"
    "Surface disagreements between sources explicitly."
)


class SynthesizerInput(BaseModel):
    question: str
    sub_queries: list[str]
    sources: list[NumberedSource]


class SynthesizerAgent:
    name = "synthesizer"

    async def stream(self, inp: SynthesizerInput) -> AsyncIterator[str]:
        catalog = "\n".join(f"[{s.n}] {s.url} — {s.summary}" for s in inp.sources)
        themes = "\n".join(f"- {q}" for q in inp.sub_queries)
        user = (
            f"Question: {inp.question}\n\n"
            f"Sub-queries researched:\n{themes}\n\n"
            f"NUMBERED SOURCE CATALOG (cite by these numbers):\n{catalog}"
        )
        # Failover-aware: only the initial request can 429 — the pool rotates
        # there; an already-started stream never needs rescuing.
        stream = await create_chat_completion(
            max_tokens=4000,
            stream=True,
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": user},
            ],
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
