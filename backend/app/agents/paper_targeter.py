"""PaperTargeterAgent — decides which single paper a question is about.

Retrieval is a global top-k across the whole project, so a question about one
paper also retrieves near-identical passages from every other paper on the
same topic. Live measurement: asked for one paper's reward functions, the
assistant answered with a DIFFERENT paper's reward table — real values,
honestly cited, wrong paper. Two attempts to forbid that in the chat system
prompt failed; this moves the decision into code, where scoping retrieval to
the identified paper makes the error structurally impossible.

This agent sees paper TITLES ONLY — never abstracts, never chunk text. That
is what keeps its cost O(1) in library size. The retrieval planner it borrows
its position from failed for exactly the opposite reason: its prompt carried
every paper's title and 300-char abstract, ~13k tokens at 100 papers.

Fail-open: on any error, return None, which means unscoped global retrieval —
the behaviour that existed before this agent.
"""

from pydantic import BaseModel

from app.core.config import settings
from app.core.logging import log
from app.llm.structured import parse_structured

SYSTEM = (
    "Decide whether the user's question is about ONE specific paper from the "
    "candidate list.\n\n"
    "Return that paper's paper_id when the question names it, describes it "
    "distinctly, or refers to a paper established earlier in the conversation. "
    "Return an EMPTY paper_id when the question is general, spans several "
    "papers, or does not identify one — an empty answer is normal and expected, "
    "and is always better than guessing, because the answer will be built only "
    "from the paper you name.\n\n"
    "Never return a paper_id that is not in the candidate list. Do not answer "
    "the user's question; only identify the paper."
)


class TargeterInput(BaseModel):
    query: str
    candidates: list[dict]  # [{"paper_id": "...", "title": "..."}]
    prior_messages: list[dict]  # [{"role": "user"|"assistant", "content": "..."}]


class TargetedPaper(BaseModel):
    paper_id: str = ""


class PaperTargeterAgent:
    name = "paper_targeter"

    async def run(self, inp: TargeterInput) -> str | None:
        if not inp.candidates:
            return None
        # Titles and ids only. Building this list explicitly rather than
        # dumping each candidate dict is what keeps an abstract or a chunk of
        # body text from reaching the prompt if a caller ever passes richer
        # dicts — see test_targeter_prompt_carries_titles_only.
        offered = {c["paper_id"] for c in inp.candidates}
        catalog = "\n".join(f"[{c['paper_id']}] {c['title']}" for c in inp.candidates)
        history = (
            "\n".join(
                f"[{m['role'].upper()}]: {m['content'][:300]}"
                for m in inp.prior_messages[-6:]  # last 6 messages max
            )
            or "(no prior conversation)"
        )
        user = (
            f"PRIOR CONVERSATION:\n{history}\n\n"
            f"CANDIDATE PAPERS:\n{catalog}\n\n"
            f"QUESTION: {inp.query}"
        )
        try:
            out = await parse_structured(
                system=SYSTEM,
                user=user,
                output_model=TargetedPaper,
                max_tokens=settings.paper_targeter_max_tokens,
            )
        except Exception as exc:
            log.warning("paper_targeter_failed_open", error=str(exc)[:200])
            return None
        paper_id = out.paper_id.strip()
        if paper_id not in offered:
            # Covers both "no paper identified" (empty) and an invented id.
            # Scoping to an id we never offered would retrieve nothing, or
            # another project's paper.
            return None
        return paper_id
