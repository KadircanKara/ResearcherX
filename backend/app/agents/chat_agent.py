"""ChatAgent — streams a grounded response from paper chunks + conversation history."""

from collections.abc import AsyncIterator

from pydantic import BaseModel, Field

from app.core.config import settings
from app.llm.client import create_chat_completion

SYSTEM = (
    "You are a research assistant with access to excerpts from academic papers. "
    "Answer the user's question using ONLY the provided paper excerpts and prior "
    "conversation. Cite claims inline with the excerpt number: [1], [2], etc.\n\n"
    "Citation rules:\n"
    "- Every non-trivial claim MUST cite its source excerpt number.\n"
    "- Use ONLY numbers from the provided EXCERPT CATALOG. Never invent numbers.\n"
    # EVERY sentence, not "every claim" — the previous wording was already
    # "every non-trivial claim MUST cite", and the model satisfied it by
    # writing uncited bullets followed by one sentence naming every source at
    # once. Measured 2026-08-22: only 39% of SUPPORTED claims carried a marker,
    # and the single worst grounding failure in the whole run was one of those
    # summarising sentences ("these observations are summarized in excerpts
    # [1]..[5], which include related figures") — a claim about the catalog
    # that no excerpt supports. So the rule now names the anti-pattern.
    "- Put each marker in the SENTENCE that makes the claim, not in a summary "
    "at the end. Every bullet in a list carries its own marker. Never write a "
    "trailing sentence that lists sources ('these observations are summarized "
    "in excerpts [1], [2], [3]') — a reader cannot tell which excerpt carried "
    "which claim, and that sentence is itself a claim no excerpt supports.\n"
    # NO FALLBACK OF ANY KIND. This replaced a decline-then-hand-off template
    # ("The assigned papers do not appear to cover this. Based on general
    # knowledge: ...") on 2026-08-22. Measured on the off_topic negatives: the
    # model obeyed that template exactly, and 72% of its claims on those cases
    # were ungrounded-by-design — uncited FAA certification requirements,
    # lithium-polymer chemistry, named FPV products — behind a single
    # disclaiming sentence a reader can easily miss. The product answers from
    # the ingested corpus or it does not answer.
    "- NEVER answer from general knowledge. Every statement you make must come "
    "from the provided excerpts, the PAPERS block, or the prior conversation. "
    "If they do not answer the question, reply exactly: 'The ingested documents "
    "do not cover this.' and STOP. Do not follow it with 'however', 'based on', "
    "'in general', or any other hand-off — there is no other source. Do not "
    "explain what the papers are about instead, and do not offer to answer from "
    "elsewhere.\n\n"
    # Retained after the no-fallback rule above subsumed it, because the
    # failure it fixes is a different one and was live-verified: the model
    # declined a paper's own year and then mined a bibliography excerpt for a
    # year-shaped number. The general rule permits the excerpts; for these
    # three fields the excerpts are themselves a wrong source.
    "Exception — authors, year, venue: for these three fields only, not even "
    "the excerpts are a source. If the PAPERS block does not state one, say the "
    "paper does not state it, and end the reply there. Do not follow that "
    "sentence with 'however', 'based on', or any other hand-off — for these "
    "three fields none exists.\n\n"
    # Without this paragraph the model declines metadata questions even with the
    # block in front of it: the authors are not in any excerpt, and the rule
    # above tells it that means it cannot answer.
    "The PAPERS block lists the title — and, where known, the authors, year, and "
    "venue — of every paper assigned to this project. It is the ONLY source for "
    "a paper's own authors, year, and venue: never answer those three from the "
    "excerpts, even when one appears to contain an answer. Excerpts are chunks "
    "of the paper's body text, and a year or venue name inside one overwhelmingly "
    "belongs to a cited work in its reference list, not to the paper itself. "
    "Answer who wrote a paper, when it was published, or where it appeared "
    "directly from the PAPERS block; it carries no excerpt numbers, so an answer "
    "drawn from it takes no citation. If a field is missing from a paper's line, "
    "the paper does not state it — full stop. Say so plainly; never infer it "
    "from excerpt content.\n\n"
    # ORDER IS DELIBERATE — do not move this paragraph before the one above it.
    # Placed earlier (before the PAPERS block is even defined), the model read
    # this conditional "ask which paper" first and then hit the paragraph
    # above's unconditional "answer ... directly from the PAPERS block" — the
    # later, unconditional instruction won, and disambiguation stopped firing
    # entirely (live-verified regression, fix round 2). Here, after the block's
    # own definition, this paragraph reads as a qualification of "answer
    # directly from the block" rather than something that instruction
    # overrides. The sequencing of this whole section — exception, then
    # PAPERS-block definition, then this paragraph — is empirically
    # established; resequencing it is not a cosmetic change.
    "When a question about authors, year, or venue does not say which paper it "
    "means and the PAPERS block lists more than one, ask which paper they mean "
    "and list only the titles — no authors, no year, no venue, and no other "
    "metadata. The field being asked about must not appear anywhere in that "
    "reply; asking the question and then answering it defeats the point. Do "
    "not answer for all of them and do not guess. If the block lists exactly "
    "one paper, or the question names or clearly implies a paper, or it asks "
    "about all of them, answer without asking. If the user already named a "
    "paper earlier in this conversation, use it — never ask twice. The PAPERS "
    "block is an internal structure; never name it in a reply — say 'the "
    "paper' or 'the papers' instead.\n\n"
    # Placed AFTER the metadata sequence, never inside it: that block carries
    # its own ORDER IS DELIBERATE warning and two live-verified regressions.
    #
    # Measured 2026-08-22: all five figure cases in the golden set had the
    # describing text retrieved, and two still produced an ungrounded claim —
    # so this is an over-claiming failure, not a retrieval one. The exact
    # shape, from `brkga-convergence-figure`: the model quoted the caption
    # correctly ("BRKGA convergence rates for three-UAV cases, based on 10
    # runs") and then added "demonstrates how the algorithm's performance
    # improves over iterations", which no sentence in the paper states. That is
    # the model reasoning about a plot it cannot see, and it is the single most
    # plausible-sounding hallucination this system produces.
    "Figures and tables — you cannot see them. Only a caption and the sentences "
    "around it reach you as text. Answer a question about a figure using ONLY "
    "what the text states about it: the caption's own words, and any sentence "
    "that discusses it. Never describe what a plot shows, which way a curve "
    "moves, what a trend demonstrates, or what a figure proves, unless a "
    "sentence says so in those terms — describing a 'convergence' plot as "
    "showing performance improving over iterations is reading the image, not "
    "the text. If the excerpts name a figure but do not describe what it "
    "shows, say the papers do not describe it and stop there.\n\n"
    # The client renders this with react-markdown + remark-gfm inside `prose`
    # classes, so GitHub-flavoured markdown renders. Raw HTML is escaped by
    # design (react-markdown v9 default, and rehype-raw must never be added —
    # this text is LLM-derived), so ask for markdown syntax only.
    "Formatting — reply in GitHub-flavoured Markdown:\n"
    "- **Bold** key terms, metric names, and numeric values.\n"
    "- Use `-` bullet lists for enumerations such as reward components, "
    "parameters, or steps. One item per line.\n"
    "- Use a Markdown table when comparing two or more things across the same "
    "dimensions (methods, papers, parameter sets).\n"
    "- Use `##` subheadings only when the answer covers several distinct topics.\n"
    "- Use backticks for symbols and identifiers, e.g. `NSGA-II`, `tau`.\n"
    "- Never emit raw HTML; it is escaped and will show as literal text.\n"
    "- Keep citations inline in the prose, e.g. '... exploring new cells (+2) [6]'. "
    "Do not put them in their own column or footnote section.\n\n"
    "Be concise. Prefer a short list or table over a long paragraph, but do not "
    "add structure to an answer that is genuinely one sentence."
)


class ChunkContext(BaseModel):
    n: int  # citation number shown to user, e.g. [1]
    paper_id: str
    title: str
    chunk_index: int
    text: str


class PaperMetaContext(BaseModel):
    """One assigned paper's structured metadata, for the PAPERS block."""

    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str | None = None


def build_papers_block(papers: list[PaperMetaContext]) -> str:
    """One compact line per assigned paper — roughly 30 tokens each.

    A field with no value is omitted entirely. Printing "Authors: unknown"
    reads to the model as a discovered fact about the paper rather than as an
    absence in our own record, and it will repeat it back to the user as one.
    """
    if not papers:
        return ""
    lines = []
    for p in papers:
        # Collapse whitespace (including embedded newlines) so a title can
        # never forge an extra "- " line or split the PAPERS block's
        # one-line-per-paper structure.
        parts = [f'"{" ".join(p.title.split())}"']
        authors = [a for a in p.authors if a and a.strip()]
        if authors:
            parts.append("Authors: " + ", ".join(authors))
        if p.year is not None:
            parts.append(str(p.year))
        if p.venue:
            parts.append(p.venue)
        lines.append("- " + " — ".join(parts))
    return "PAPERS ASSIGNED TO THIS PROJECT:\n" + "\n".join(lines)


def build_scope_block(
    titles: list[str],
    widened: bool,
    empty_titles: list[str] | None = None,
    scope_source: str = "mention",
) -> str:
    """Tell the model what this turn was restricted to, and by what.

    `scope_source` is the PROVENANCE and it is not decoration. "mention" means
    the user picked these papers in the composer; "resolved" means the
    question's own words named them (`paper_resolver`) and the user picked
    nothing. Saying "the user restricted this question to" about a resolved
    scope is simply false, and a model told the user made a choice they did
    not make will defend that choice back at them.

    Without it the model cannot distinguish a narrow evidence set from a thin
    one, and SYSTEM's "the assigned papers do not appear to cover this" rule
    fires as a false statement about the whole library.

    `empty_titles` are papers the user named that returned NO chunks -- their
    nearest chunk fell outside the distance gate, or they were never ingested.
    They are marked rather than dropped: the model is told the user named them,
    so silence about them reads as "this paper does not discuss X" when the
    truth is "I was handed nothing from it". Marking the gap is what lets the
    model say so instead of inventing a reason. A named paper contributing
    nothing is currently REACHABLE and silent -- see the multi-mention gate
    note in CLAUDE.md.

    Collapses whitespace in each title for the same reason build_papers_block
    does: a title carrying a newline could otherwise forge a line of this
    block.
    """
    if not titles:
        return ""
    empty = {" ".join(t.split()) for t in (empty_titles or [])}
    lines = []
    for title in titles:
        clean = " ".join(title.split())
        lines.append(f"- {clean} — no excerpts retrieved" if clean in empty else f"- {clean}")
    tail = (
        "Excerpts from other papers are also provided; use them for comparison."
        if widened
        else "Answer from these. Excerpts from other papers are not available this turn."
    )
    if empty:
        tail += (
            " Where a paper is marked no excerpts were retrieved, say that nothing from it"
            " was available rather than describing what it does or does not contain."
        )
    header = (
        "SCOPE: the question names these papers:"
        if scope_source == "resolved"
        else "SCOPE: the user restricted this question to:"
    )
    return header + "\n" + "\n".join(lines) + f"\n{tail}"


class ChatAgentInput(BaseModel):
    query: str
    prior_messages: list[dict]  # [{"role": "user"|"assistant", "content": "..."}]
    paper_chunks: list[ChunkContext]
    # Defaulted so existing callers keep working. Titles always ship; whether
    # authors/year/venue are populated too is now decided per turn by the
    # caller (needs_paper_metadata in chat_service.py) before this input is
    # even built -- this model has no opinion on that decision, only on how
    # to render whatever PaperMetaContext list it is handed.
    papers: list[PaperMetaContext] = Field(default_factory=list)
    # Titles the user named with "@" this turn, and whether the widener let the
    # rest of the library in beside them. Empty means unscoped.
    scope_titles: list[str] = Field(default_factory=list)
    scope_widened: bool = False
    # How the scope was set: "mention" (the user picked them) or "resolved"
    # (the question's own words named them). Defaults to "mention" so every
    # existing caller keeps its current wording.
    scope_source: str = "mention"
    # Mentioned papers that returned no chunks at all. Marked in the SCOPE
    # block so the model reports the gap instead of describing a paper it was
    # handed nothing from.
    scope_empty_titles: list[str] = Field(default_factory=list)


class ChatAgent:
    name = "chat"

    async def stream(self, inp: ChatAgentInput) -> AsyncIterator[str]:
        if inp.paper_chunks:
            catalog = "\n\n".join(
                f"[{c.n}] From '{c.title}' (chunk {c.chunk_index}):\n{c.text}"
                for c in inp.paper_chunks
            )
            context_block = f"EXCERPT CATALOG:\n{catalog}"
        else:
            # Nothing cleared the similarity threshold. That is not a licence
            # to answer from memory: an empty catalog is precisely the state
            # the refusal exists for, and the previous text here ("answer from
            # general knowledge") contradicted the system prompt outright.
            context_block = (
                "EXCERPT CATALOG: (empty — no excerpt in the library was similar enough "
                "to this question. Unless the PAPERS block answers it, reply exactly "
                "'The ingested documents do not cover this.' and stop.)"
            )

        # Build conversation history for multi-turn context
        messages = [{"role": "system", "content": SYSTEM}]
        for m in inp.prior_messages:
            messages.append({"role": m["role"], "content": m["content"]})
        blocks = []
        papers_block = build_papers_block(inp.papers)
        if papers_block:
            blocks.append(papers_block)
        scope_block = build_scope_block(
            inp.scope_titles, inp.scope_widened, inp.scope_empty_titles, inp.scope_source
        )
        if scope_block:
            blocks.append(scope_block)
        blocks.append(context_block)
        messages.append(
            {
                "role": "user",
                "content": "\n\n".join(blocks) + f"\n\nQUESTION: {inp.query}",
            }
        )

        stream = await create_chat_completion(
            max_tokens=settings.chat_answer_max_tokens,
            stream=True,
            messages=messages,
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
