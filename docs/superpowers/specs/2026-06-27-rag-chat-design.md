# RAG Chat Feature Design

**Date:** 2026-06-27
**Branch target:** `feat/l2-paper-discovery` (or dedicated `feat/l3-rag-chat`)
**Status:** Approved

---

## Overview

Replace the project Chat tab with a multi-conversation RAG chatbot. Users ask natural-language questions; the system retrieves semantically relevant chunks from assigned project papers and relevant prior conversation history, then generates a grounded response with inline citations.

The current run list moves to Explorer → "Research" view (separate, future work). The Chat tab is entirely replaced.

---

## Section 1: Data Model

### New Tables

```sql
-- Research papers assigned to a project
papers (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id  UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  title       TEXT NOT NULL,
  abstract    TEXT,
  pdf_url     TEXT,                      -- original URL or null for upload-only
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)

-- Multi-conversation threads per project
chat_conversations (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id     UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  title          TEXT NOT NULL,          -- truncated first user message
  created_by     UUID NOT NULL REFERENCES users(id),
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
)

-- Individual messages within a conversation
chat_messages (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID NOT NULL REFERENCES chat_conversations(id) ON DELETE CASCADE,
  role            TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
  content         TEXT NOT NULL,
  citations       JSONB NOT NULL DEFAULT '[]',  -- [{n, paper_id, chunk_index, text_snippet}]
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
)

-- Semantic index over paper text
paper_chunk_embeddings (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  paper_id    UUID NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  chunk_index INT NOT NULL,
  text        TEXT NOT NULL,
  embedding   vector(768) NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (paper_id, chunk_index)
)

-- Semantic index over conversation history
conversation_message_embeddings (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  message_id UUID NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
  embedding  vector(768) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
```

### Indexes

```sql
CREATE INDEX ON paper_chunk_embeddings
  USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

CREATE INDEX ON conversation_message_embeddings
  USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
```

### Chunking Strategy

PDF text split with sliding window: **512 tokens, 64-token overlap**. Overlap preserves sentence boundaries that fall on chunk edges. Chunks stored in order; `chunk_index` is stable after initial ingest.

---

## Section 2: Backend Architecture

### Embedding Provider

Gemini `text-embedding-004` via Google's OpenAI-compatible endpoint:
- Base URL: `https://generativelanguage.googleapis.com/v1beta/openai/`
- Model: `text-embedding-004`
- Dimensions: 768
- `task_type=RETRIEVAL_DOCUMENT` when indexing (paper chunks, messages)
- `task_type=RETRIEVAL_QUERY` when embedding a user query

Configured via `EMBEDDING_BASE_URL` / `EMBEDDING_API_KEY` / `EMBEDDING_MODEL` env vars (separate from chat LLM vars so providers can differ). Must be added to `.env.example` and `app/core/config.py`. Default values point to Gemini endpoint.

### New Services

**`EmbeddingService`** (`app/services/embedding_service.py`)
- `embed_document(text: str) → list[float]` — RETRIEVAL_DOCUMENT task type
- `embed_query(text: str) → list[float]` — RETRIEVAL_QUERY task type
- Single httpx call to Gemini endpoint; no SDK dependency — avoids coupling to OpenAI client session state

**`PaperIngestService`** (`app/services/paper_ingest_service.py`)
- `ingest(db, paper_id, pdf_bytes) → int` — extracts text, chunks, embeds, persists
- Uses `pymupdf` for PDF text extraction (IEEE double-column aware: reads column order correctly via `sort=True`)
- Idempotent: existing chunks for a paper are replaced on re-ingest

**`ConversationService`** (`app/services/conversation_service.py`)
- `create_conversation(db, project_id, user_id, first_message) → ChatConversation`
- `list_conversations(db, project_id) → list[ChatConversation]`
- `get_conversation(db, conversation_id) → ChatConversation | None` — with messages
- `save_message(db, conversation_id, role, content, citations) → ChatMessage`
- On save: fire-and-forget background task embeds the message (non-blocking to SSE stream)

**`ChatService`** (`app/services/chat_service.py`)
- `respond(conversation_id, user_message_id) → AsyncGenerator[str, None]` — orchestrates full pipeline, yields SSE events

### New Agents

**`RetrievalPlannerAgent`** (`app/agents/retrieval_planner.py`)
- Input: query + paper list (id/title/abstract) + retrieved prior messages
- Output: `RetrievalPlan(mode, reformulated_query, per_paper: list[PaperAlloc])`
- Uses `parse_structured()` — same JSON-mode + schema-in-prompt + retry pattern as existing agents
- Fail-open: error → `mode=broad`, 2 chunks per paper, original query unchanged

**`ChatAgent`** (`app/agents/chat_agent.py`)
- Input: query + assembled context (prior messages + paper chunks) + citation map
- Streaming output via `stream()` method — same pattern as `SynthesizerAgent`
- System prompt instructs: cite with `[n]`, grounded only in provided context, say "not covered in the assigned papers" when context is absent

---

## Section 3: Retrieval Pipeline

```
User message arrives
  ↓
embed query (RETRIEVAL_QUERY)
  ↓
SSE: {type: "thinking"}
  ↓
[parallel]
  A. Semantic search over conversation_message_embeddings
     → top-5 messages with cosine similarity > 0.5
     → formatted as "[User]: ..." / "[Assistant]: ..." pairs
  
  B. [only if project has ≥3 assigned papers]
     RetrievalPlannerAgent(query, paper_list, retrieved_history)
     → RetrievalPlan
     [<3 papers: skip planner, use mode=broad, 2 chunks/paper, raw query]
  ↓
SSE: {type: "retrieving", paper_count: N}
  ↓
Per-paper pgvector search using reformulated_query embedding
  capped at per_paper[paper_id].chunks (minimum 1 per paper)
  ↓
Threshold routing:
  Path A: ≥1 chunk with similarity ≥ 0.75 → full RAG context
  Path B: all chunks for a paper below threshold → skip that paper's chunks
  Path C: total qualifying chunks < 2 → fallback (no paper context, ChatAgent
          answers from prior conversation + general knowledge, notes gap)
  ↓
Assemble context:
  [relevant prior messages, oldest→newest]
  [paper chunks, ordered by paper then chunk_index]
  Soft cap: total context ≤ 8,000 tokens; truncate lowest-similarity chunks first
  ↓
ChatAgent.stream(context) → yield SSE delta events
  ↓
Post-process: validate citation indices [n] ≤ number of chunks provided
  replace invalid citations with [source unavailable]
  ↓
persist assistant message + citations
embed assistant message (async, non-blocking)
SSE: {type: "done", citations: [...]}
```

---

## Section 4: Retrieval Planner System Prompt

```
You are a retrieval planner for a project-scoped research assistant.
Your job: decide how many text chunks to retrieve from each assigned paper
to best answer the user's query.

USER QUERY:
{query}

RELEVANT PRIOR CONVERSATION (for context only — do not answer from this):
{retrieved_prior_messages}

ASSIGNED PAPERS:
[{id}] {title}
Abstract: {abstract[:300]}
...

INSTRUCTIONS:
Choose a retrieval mode and allocate chunks per paper. Rules:

Mode "comparative": user compares/contrasts across papers, uses words like
  "compare", "difference", "all papers", "each" → 3 chunks each, ALL papers

Mode "targeted": query references a specific paper by name, or asks about
  a narrow concept likely in one paper → 5 chunks from that paper,
  1 chunk from all others as a baseline (never skip a paper entirely)

Mode "broad": general factual question, concept definition, overview →
  2–3 chunks each, all papers

Additional rules:
- Never output 0 chunks for any paper. Relevant content can be absent from
  an abstract but present in the body. Minimum is 1.
- Use prior conversation to resolve pronouns ("it", "that method", "the paper")
  before deciding mode and before writing reformulated_query.
- reformulated_query: rewrite the query for better vector search — expand
  abbreviations, replace pronouns with their referents, add synonyms.
  If the query is already clear and specific, copy it unchanged.

Output ONLY valid JSON, no explanation:
{
  "mode": "comparative" | "targeted" | "broad",
  "reformulated_query": "...",
  "per_paper": [{"paper_id": "...", "chunks": N}, ...]
}
```

---

## Section 5: API

```
POST   /v1/projects/{id}/conversations
       body: {}
       → ChatConversationOut

GET    /v1/projects/{id}/conversations
       → list[ChatConversationOut]

GET    /v1/projects/{id}/conversations/{cid}
       → ChatConversationDetailOut   (includes messages)

POST   /v1/projects/{id}/conversations/{cid}/messages
       body: {content: str}
       → SSE stream

POST   /v1/projects/{id}/papers/{pid}/ingest
       body: multipart/form-data (PDF file)
       → {chunks_stored: int}
```

### SSE Event Types (chat stream)

| type | payload |
|------|---------|
| `thinking` | `{}` |
| `retrieving` | `{paper_count: int, history_hits: int}` |
| `delta` | `{text: str}` |
| `done` | `{citations: [{n, paper_id, title, chunk_index, snippet}]}` |
| `error` | `{message: str}` — generic, no internals |

All event types registered in both the switch and addEventListener list in the frontend stream component (per CLAUDE.md rule).

### Auth / access control

Conversation and message endpoints require project membership (viewer+). Paper ingest requires editor+. All go through `get_current_user_optional` → 401 if no identity + project-scoped request.

---

## Section 6: Frontend

### Chat Tab Layout

```
┌─────────────────────────────────────────────────────────────┐
│ [+ New Chat]                    Conversations               │
│ ─────────────────────────────────────────────────────────── │
│ ▸ Compare UAV methods (Jun 27)                              │
│   What is MTSP? (Jun 26)                                    │
│                                                             │
│ ─────────────────────────────────────────────────────────── │
│                     Message thread                          │
│                                                             │
│  User: Compare the methods proposed in all papers...        │
│                                                             │
│  Assistant: The two papers approach multi-UAV ...           │
│             [1] [2]                                         │
│                                                             │
│ ─────────────────────────────────────────────────────────── │
│ [ Type a question...                          ] [Send]      │
└─────────────────────────────────────────────────────────────┘
```

### Components

- `ChatTab` — layout shell, conversation sidebar + thread pane
- `ConversationList` — fetches `GET /conversations`, "New Chat" creates then navigates
- `MessageThread` — renders message history, streams new assistant response via `ChatStream`
- `ChatStream` — SSE subscriber, same subscribe-first-then-seed pattern as `RunStream`
- `CitationChip` — `[n]` renders inline; hover shows paper title + 2-line snippet

### New routes

```
/research/[id]/chat/page.tsx               → ChatTab (replaces run list)
/research/[id]/chat/[cid]/page.tsx         → conversation detail (or same page with cid param)
```

### Frontend types to add

```typescript
interface ChatConversation {
  id: string; title: string; created_at: string; updated_at: string;
}
interface ChatMessage {
  id: string; role: "user" | "assistant"; content: string;
  citations: Citation[]; created_at: string;
}
interface Citation {
  n: number; paper_id: string; title: string;
  chunk_index: number; snippet: string;
}
type ChatEvent =
  | { type: "thinking" }
  | { type: "retrieving"; paper_count: number; history_hits: number }
  | { type: "delta"; text: string }
  | { type: "done"; citations: Citation[] }
  | { type: "error"; message: string };
```

---

## Out of Scope

- Explorer "Research" view (run list migration) — separate future task
- Paper management UI (upload/assign) — papers can be seeded via `POST /ingest` directly
- Streaming citation links mid-response (citations delivered at `done` event only)
- Per-message feedback / thumbs up-down
