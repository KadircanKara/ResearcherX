# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Graduate students and academics (PhD students, postdocs, faculty), often working in small lab groups. They are in the middle of a literature review or a paper: collecting the papers of a field, asking questions across them, and writing a manuscript that cites them.

## Product Purpose

ResearcherX takes a researcher from reading to writing in one workspace. Papers go into a project library (PDF upload, URL, or manual entry), are split along their own sections and indexed, and can then be questioned in a chat whose answers cite the exact passages they rest on. The same project holds a multi-file LaTeX editor with a sandboxed compiler, so the manuscript is written next to the sources it draws on.

Success: a researcher builds a project's library, gets grounded answers they can check against the source, and writes the paper without leaving the project.

## Positioning

Read-to-write in one place. The library, the grounded chat and the LaTeX editor share one project, so the loop from finding a claim in a paper to writing it into a manuscript never crosses tools. Grounding is what makes the loop trustworthy: answers cite the passage (with section and page) for every sentence, and when the library does not cover a question the chat says so instead of answering from general knowledge.

## Operating Context

- Work is organised into projects, each with its own paper library, conversations and LaTeX documents, shared among project members (owner and member roles; LaTeX documents add editor and viewer access).
- Chat scope is explicit: `@` mentions restrict a question to named papers, a question that names a paper by title or author is scoped to it, otherwise the whole library is searched. The UI tells the user when a search was narrowed.
- LaTeX documents are file trees (multi-file, figures, bibliography), compiled with pdflatex or xelatex, with SyncTeX jumps between source and PDF, and zip import/export compatible with Overleaf-style projects.
- Explorer (searching beyond the library for papers to add) and the paper graph exist as design previews on sample data.

## Capabilities and Constraints

- Shipped: paper ingest (PDF/URL/manual), section-bounded chunking, hybrid retrieval with reranking, RAG chat with per-sentence citations and hover cards, `@` mentions, conversation export, project and document sharing, multi-file LaTeX editing and compiling.
- Not yet built: authentication (every request resolves to a seeded user today), a real Explorer backend, a similarity backend for the graph.
- The marketing landing page lives at `/`; the app lives under `/admin`.
- Stack is established: Next.js 15 + React 19 + Tailwind 3 + shadcn on Base UI (frontend), FastAPI + Postgres/pgvector (backend).
- Stage: a public launch with sign-ups is planned; auth, the production compiler service and rate limits on chat are prerequisites.

## Brand Commitments

- Name: ResearcherX.
- The chat's refusal wording is fixed: "The ingested documents do not cover this."
- Citation markers read `[n]`, with the locator written as `Section > Subsection · p. N`.

## Evidence on Hand

- A real 102-paper Multi-UAV Coordination library in the dev database, with conversations and a compiled IEEE-format LaTeX paper.
- Retrieval and groundedness eval harnesses with recorded measurements (`backend/evals/`).
- No customers, testimonials, usage metrics or press yet. Future work must not fabricate any.

## Product Principles

1. Every answer is checkable: a claim without a source passage is not shown as grounded.
2. Scope is chosen by the user or by words they typed, never guessed; a narrowed search is always disclosed.
3. Reading and writing belong in the same project; moving from a passage to the manuscript should never require another tool.
4. Refuse honestly rather than answer confidently from outside the library.
5. Nothing the user wrote is lost: autosave, explicit conflicts, and no silent overwrites.
