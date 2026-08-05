"""ChatAgent — streams a grounded response from paper chunks + conversation history."""

from collections.abc import AsyncIterator

from pydantic import BaseModel

from app.llm.client import create_chat_completion

SYSTEM = (
    "You are a research assistant with access to excerpts from academic papers. "
    "Answer the user's question using ONLY the provided paper excerpts and prior "
    "conversation. Cite claims inline with the excerpt number: [1], [2], etc.\n\n"
    "Citation rules:\n"
    "- Every non-trivial claim MUST cite its source excerpt number.\n"
    "- Use ONLY numbers from the provided EXCERPT CATALOG. Never invent numbers.\n"
    "- If the answer cannot be found in the excerpts, say: "
    "'The assigned papers do not appear to cover this. Based on general knowledge: ...'\n\n"
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


class ChatAgentInput(BaseModel):
    query: str
    prior_messages: list[dict]  # [{"role": "user"|"assistant", "content": "..."}]
    paper_chunks: list[ChunkContext]


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
        messages.append(
            {
                "role": "user",
                "content": f"{context_block}\n\nQUESTION: {inp.query}",
            }
        )

        stream = await create_chat_completion(
            max_tokens=2000,
            stream=True,
            messages=messages,
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
