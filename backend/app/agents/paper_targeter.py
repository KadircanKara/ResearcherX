"""PaperTargeterAgent — decides which paper(s) a question is about.

Retrieval is a global top-k across the whole project, so a question about one
paper also retrieves near-identical passages from every other paper on the
same topic. Live measurement: asked for one paper's reward functions, the
assistant answered with a DIFFERENT paper's reward table — real values,
honestly cited, wrong paper. Two attempts to forbid that in the chat system
prompt failed; this moves the decision into code.

Rung 2 of the resolver ladder (`app/services/paper_resolver.py` is rung 1).
The list is what makes a comparison question answerable: a question naming two
papers used to return an empty answer here, which left scope at the whole
project and produced asymmetric evidence — the paper whose vocabulary matched
the phrasing took nearly every slot.

This agent sees paper TITLES ONLY — never abstracts, never chunk text. That
is what keeps its cost O(1) in library size.

Fail-open: on any error, return [], which means the next rung down —
unscoped global retrieval, the behaviour that existed before this agent.
"""

from pydantic import BaseModel

from app.core.config import settings
from app.core.logging import log
from app.llm.structured import parse_structured

SYSTEM = (
    "Decide which papers from the candidate list the user's question is "
    "about.\n\n"
    "Return the paper_id of EVERY candidate the question is about — one id "
    "for a question about a single paper, several for a question that "
    "compares or spans them. Return an EMPTY list when the question is "
    "general or does not identify any paper — an empty answer is normal and "
    "expected, and is always better than guessing, because the answer will be "
    "built only from the papers you name.\n\n"
    "Never return a paper_id that is not in the candidate list. Do not answer "
    "the user's question; only identify the papers."
)


class TargeterInput(BaseModel):
    query: str
    candidates: list[dict]  # [{"paper_id": "...", "title": "..."}]
    prior_messages: list[dict]  # [{"role": "user"|"assistant", "content": "..."}]


class TargetedPaper(BaseModel):
    paper_ids: list[str] = []


class PaperTargeterAgent:
    name = "paper_targeter"

    async def run(self, inp: TargeterInput) -> list[str]:
        if not inp.candidates:
            return []
        try:
            # Titles and ids only. Building this list explicitly rather than
            # dumping each candidate dict is what keeps an abstract or a
            # chunk of body text from reaching the prompt if a caller ever
            # passes richer dicts — see test_targeter_prompt_carries_titles_only.
            #
            # Inside the try on purpose: a candidate or history dict missing
            # an expected key must fail open to [] like every other error
            # here, not raise KeyError out of run() and fail the whole turn.
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
            out = await parse_structured(
                system=SYSTEM,
                user=user,
                output_model=TargetedPaper,
                max_tokens=settings.paper_targeter_max_tokens,
            )
        except Exception as exc:
            log.warning("paper_targeter_failed_open", error=str(exc)[:200])
            return []
        # Dedupe preserving the model's order and drop anything not offered:
        # an id we never offered would retrieve nothing, or another project's
        # paper. No cap here -- how many papers a question may scope to is
        # policy, and it lives with the other scope decisions in chat_service.
        seen: set[str] = set()
        chosen: list[str] = []
        for raw in out.paper_ids:
            paper_id = raw.strip()
            if paper_id in offered and paper_id not in seen:
                seen.add(paper_id)
                chosen.append(paper_id)
        return chosen
