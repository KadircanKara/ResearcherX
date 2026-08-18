"""ScopeWidenerAgent — decides whether a mentioned-papers turn ALSO needs the library.

This agent may only WIDEN. It has no vote on whether the papers the user named
are retrieved: those are always retrieved, and the widener answers exactly one
question — is that enough, or does the turn also need the rest of the project?

The asymmetry is the design. An LLM that could NARROW scope would reintroduce
the failure this whole feature replaced: a titles-only model picking a wrong
paper and retrieval being scoped to it (measured wrong on 11 of 30 golden
questions, 2026-08-18).

Titles only, and no conversation history: cost stays O(1) in library size, and
scope stays per-turn, so an earlier turn cannot widen a later one.

Fail-open: any error means widen=False — answer from what the user pointed at.
"""

from pydantic import BaseModel

from app.core.config import settings
from app.core.logging import log
from app.llm.structured import parse_structured

SYSTEM = (
    "The user asked a question and explicitly restricted it to specific papers.\n\n"
    "Those papers WILL be used regardless of your answer. Decide only whether "
    "answering also requires searching the rest of the project's papers.\n\n"
    "Return widen=true when the question compares the named papers to other "
    "work, asks what else exists, asks whether other studies do something, or "
    "otherwise cannot be answered from the named papers alone.\n"
    "Return widen=false when the question is about the named papers themselves "
    "— their methods, results, numbers, figures, or claims.\n\n"
    "Do not answer the user's question."
)


class WidenerInput(BaseModel):
    query: str
    mentioned_titles: list[str]


class WidenDecision(BaseModel):
    widen: bool = False


class ScopeWidenerAgent:
    name = "scope_widener"

    async def run(self, inp: WidenerInput) -> bool:
        titles = "\n".join(f"- {t}" for t in inp.mentioned_titles)
        user = f"PAPERS THE USER NAMED:\n{titles}\n\nQUESTION: {inp.query}"
        try:
            out = await parse_structured(
                system=SYSTEM,
                user=user,
                output_model=WidenDecision,
                max_tokens=settings.scope_widener_max_tokens,
            )
        except Exception as exc:
            log.warning("scope_widener_failed_open", error=str(exc)[:200])
            return False
        return out.widen
