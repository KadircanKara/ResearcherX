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
    "- If the answer cannot be found in the excerpts or the PAPERS block, say: "
    "'The assigned papers do not appear to cover this. Based on general knowledge: ...'\n\n"
    # The rule above is a template: decline, then hand off to another source.
    # Live testing showed the model following it to the letter for a paper's
    # own year — declining, then adding "however, based on the EXCERPT
    # CATALOG..." and fabricating one from a bibliography. For these three
    # fields there is no other source to hand off to, so the exception has
    # to name the hand-off words themselves and forbid them outright.
    "Exception — authors, year, venue: for these three fields only, there is "
    "no fallback of any kind, not general knowledge and not the excerpts. If "
    "the PAPERS block does not state one, say the paper does not state it, "
    "and end the reply there. Do not follow that sentence with 'however', "
    "'based on', or any other hand-off to another source — for these three "
    "fields none exists.\n\n"
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
            context_block = (
                "EXCERPT CATALOG: (no excerpts retrieved — answer from general knowledge)"
            )

        # Build conversation history for multi-turn context
        messages = [{"role": "system", "content": SYSTEM}]
        for m in inp.prior_messages:
            messages.append({"role": m["role"], "content": m["content"]})
        blocks = []
        papers_block = build_papers_block(inp.papers)
        if papers_block:
            blocks.append(papers_block)
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
