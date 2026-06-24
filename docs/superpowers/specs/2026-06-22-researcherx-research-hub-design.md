# ResearcherX → Research Hub — Design Spec

- **Date:** 2026-06-22
- **Status:** Approved (pending spec review)
- **Author:** Claude (design lead) + Lior
- **Supersedes:** the streaming-research-demo UI; evolves the existing backend.

---

## 1. Summary

ResearcherX becomes a **single hub for academic research and collaborative paper-writing**.
Today researchers bounce between a discovery tool (Elicit / ResearchRabbit / Connected
Papers / Semantic Scholar) and a writing tool (Overleaf). This product does both in one
workspace: discover and analyse papers, build a citation graph, and write the actual
LaTeX paper — with collaborators — without leaving.

**Positioning wedge (from competitive research):** the discovery tools don't let you
write; the writing tools don't let you discover. The few integrated attempts (OpenAI
Prism, PapersFlow, SciSpace, Authorea) each miss one of: trustworthy deeply-sourced
citations, a real citation graph, or genuine collaborative LaTeX. Our defensible
combination is **trustworthy multi-source discovery (arXiv + Semantic Scholar +
OpenAlex) + a citation graph + collaborative server-compiled LaTeX**, in one project.

## 2. Scope

### In scope (v1)
- **Explorer mode** — global paper search across arXiv + Semantic Scholar (+ OpenAlex
  fallback), per-project relevance scoring, matched/missing keyword analysis, one-click
  add / AI-suggested project routing.
- **Research mode** — Projects, each with four tabs:
  - **Chat** — Q&A grounded in the project's papers (RAG), reusing the existing
    multi-agent pipeline; streamed over SSE.
  - **Papers** — the project's papers with relevance %, matched/missing chips, add/remove.
  - **Graph** — citation/similarity graph (React Flow) with filters and a paper side
    panel (paper view / connected papers / summary / connection-analysis).
  - **LaTeX** — multi-document editor: source (CodeMirror) + KaTeX live preview +
    server-compiled PDF, version snapshots, comments with @mentions, download `.tex`.
- **Sharing & roles** — `owner | editor | commenter | viewer` per project; share modal.
- **Identity (data only)** — users, membership, authorship, mentions. See §6.3.

### Deferred (designed-for, built later)
- **Authentication** — login/signup/password/JWT. Stubbed via `get_current_user()` in
  v1 (§6.3). This is an explicit, approved deferral: get functionality running first.
- **Assistant mode** — daily-task chat + messaging-channel integrations
  (WhatsApp/Telegram/Email/LinkedIn), AI message drafting, LinkedIn post generator.
  The "project-as-context-pointer" idea is reserved in the data model.
- **Real-time multi-cursor (CRDT)** — v1 is async collaboration (autosave + versions +
  comments). Model boundaries kept clean so Yjs drops in later.

### Out of scope
- Mobile-native apps; offline mode; payment/billing; admin console.

## 3. Personas & primary flows
- **PI / researcher** — runs a project, writes the paper, invites collaborators.
- **Collaborator** — edits/comments on LaTeX, adds papers, reads the graph.
- **Student** — explores literature, routes papers into a reading-group project.

**Core flow:** Explorer search → see relevance against my projects → add paper to a
project → it appears in Papers + Graph → ask Chat about it → cite it while writing in
LaTeX → compile to PDF → share with collaborators who comment with @mentions.

## 4. Information architecture (Next.js 15 App Router)

```
/                         → redirect to /research
/explorer                 → global paper search + relevance + route-to-project
/research                 → projects list (create, search)
/research/[projectId]
    /chat                 → project chat (default tab)
    /papers               → project papers
    /graph                → citation graph + side panel
    /latex                → documents list + editor
    /latex/[docId]        → a specific document
/settings                 → profile/preferences (minimal in v1)
```
Share dialog, connection-analysis, and the dev user-switcher are modals/panels, not
routes. (`/login`, `/signup` arrive in the auth phase.)

## 5. Data model (PostgreSQL + pgvector)

Conventions follow the existing code: `String(36)` UUID PKs (`_uuid()`), status columns
as `StrEnum` over `String(16)`, **all** datetimes `DateTime(timezone=True)` (`_now()`),
**list fields stored as `JSON`** (portable to the sqlite test DB), embeddings as
`pgvector` with a test fallback (§10.3).

| Table | Key columns | Notes |
|---|---|---|
| `users` | id, email (unique), name, `password_hash` (nullable), avatar_color, created_at | password_hash nullable so auth is additive |
| `projects` | id, owner_id→users, title, description, topic_keywords(JSON), embedding(vector, nullable), created_at, updated_at | embedding derived from title+desc+keywords |
| `project_members` | id, project_id→projects, user_id→users, role `owner\|editor\|commenter\|viewer`, created_at | unique(project_id, user_id) |
| `papers` | id, source `arxiv\|semantic_scholar\|openalex`, external_id, doi (nullable), title, authors(JSON), year, abstract, citation_count, url, embedding(vector, nullable), fetched_at | global cache; unique(source, external_id); doi dedup |
| `project_papers` | id, project_id, paper_id, relevance(float), matched_keywords(JSON), missing_keywords(JSON), added_by→users, added_at, status `included\|candidate` | per-project scoring behind the Papers screen |
| `graph_edges` | id, project_id, src_paper_id, dst_paper_id, kind `citation\|similarity\|manual`, weight(float), created_at | directed; nodes = project_papers |
| `documents` | id, project_id, title, body(Text), created_by→users, created_at, updated_at | LaTeX source; latest body inline |
| `document_versions` | id, document_id, body(Text), label, created_by→users, created_at | snapshots (autosave + manual) |
| `comments` | id, document_id, author_id→users, body, anchor(JSON, nullable), resolved(bool), created_at | anchor = line/selection/section |
| `comment_mentions` | id, comment_id, user_id→users | @mention fan-out |
| `conversations` | id, project_id, title, created_by→users, created_at | project Chat threads |
| `messages` | id, conversation_id, role `user\|assistant`, content(Text), citations(JSON), run_id→research_runs (nullable), created_at | assistant messages link to a run |
| `compile_jobs` | id, document_id, status `queued\|running\|success\|error`, pdf_path (nullable), log(Text), requested_by→users, created_at, finished_at | DB-backed queue (§9.1) |

`research_runs` + `agent_steps` are unchanged and now reachable from `messages.run_id` —
the existing agent pipeline becomes the Chat execution engine.

## 6. Backend architecture (evolve FastAPI)

### 6.1 Layers
Keep the existing boundary: `api/v1 → services → agents → tools/integrations → llm/db`.
New `app/integrations/` holds external source adapters (arXiv, Semantic Scholar,
OpenAlex). Agents/tools/llm/event-bus are reused.

### 6.2 Routers (`/api/v1`)
- `projects` — CRUD, search, members (add/update-role/remove), `GET /users` (teammate
  picker; dev-seeded in v1).
- `explorer` — `GET /explorer/search?q&source&year&sort` → normalized results, each
  scored against the caller's active projects; `POST /explorer/add` (paper → project);
  suggested-project routing.
- `papers` / `project_papers` — list (filter/search), add, remove, re-score.
- `graph` — `GET /projects/:id/graph` (nodes+edges), edge CRUD, `POST .../connection-analysis`
  (AI analysis over a toggled node subset).
- `documents` — CRUD, `PUT body` (autosave), versions list/create/restore, `GET .../tex`
  (download), compile endpoints.
- `compile` — `POST /documents/:id/compile` → job; `GET /compile/:jobId`; `GET .../pdf`.
- `comments` — CRUD, resolve, mention fan-out.
- `conversations` — list/create; `POST .../messages` triggers a RAG run; SSE stream via
  the existing event bus.

### 6.3 The current-user seam (auth deferral)
A single dependency `get_current_user(request) -> User`:
- **v1 (auth deferred):** returns a seeded default user. When `environment == "dev"`,
  honors an `X-Dev-User-Id` header to "act as" a seeded teammate (powers the
  collaboration UX without login). Header is ignored outside dev.
- **Auth phase:** internals become "validate JWT from httpOnly cookie → load user";
  adds `auth` router (`signup/login/logout/me`) + frontend screens. **No core-table
  migration** (password_hash already exists, nullable).

Seed data (v1): users *You*, *Amelia Chen*, *Marco Rossi*; the three demo projects from
the mockups (Multi-UAV Coordination, Aerial Computer Vision, LLM Alignment Reading
Group) with sample papers.

### 6.4 Permissions
Every project-scoped resource is gated by membership + role, checked in the service
layer against the resolved principal — never from a client-supplied user id. Role
capability matrix: viewer (read) ⊂ commenter (+comments) ⊂ editor (+content writes) ⊂
owner (+members/sharing/delete).

### 6.5 Project Chat = RAG
A message retrieves the project's most relevant papers (pgvector over `project_papers`)
and feeds them as grounding to the existing Synthesizer/Critic pipeline; citations map
to project papers. This evolves the current web-research pipeline; it does not replace it.

### 6.6 Source integrations
Adapters normalize arXiv (Atom API), Semantic Scholar (Academic Graph API — used where
licensing allows; keys now favor institutional email), and **OpenAlex** (open, the
resilient default) into one `papers` shape. Fetched papers are cached in `papers` to
respect rate limits. Per-IP + owner-key rate limiting (existing `security.py`) stays;
per-user limits arrive with auth.

### 6.7 LaTeX compile worker
Separate non-root container running **Tectonic**, polling `compile_jobs`. Image bakes a
Tectonic package cache so compiles run offline + fast. PDF → shared volume (dev) /
object storage (prod), path recorded on the job; status surfaced via SSE/poll. Kept off
the single-worker SSE backend, per `CLAUDE.md`.

## 7. Frontend architecture (Next.js 15 + TS + Tailwind + shadcn/ui)

- **Foundation:** shadcn/ui (Radix primitives), `class-variance-authority`,
  `tailwind-merge`, `lucide-react`, `next-themes`. Tailwind stays **3.4**.
- **Theming:** two CSS-variable themes (Atlas light / Slate dark) on shadcn tokens;
  `next-themes` switch; a **compact density** toggle (data-attribute) for Papers + Graph.
- **Key libraries:** `@xyflow/react` (graph), CodeMirror 6 via `@uiw/react-codemirror`
  (+ LaTeX language), `katex` (preview), `pdfjs-dist`/`react-pdf` (compiled PDF).
  Keep `react-markdown` + `remark-gfm` (already present, used in Chat/reports; **never**
  add `rehype-raw` — that's the XSS policy).
- **Data layer:** typed client in `src/lib/` against the frozen API contract; SSE client
  (evolve existing `run-stream.tsx`) for Chat + compile status. New SSE event types must
  be registered in both the `switch` and the `addEventListener` list (existing rule).
- **Screen inventory:** app shell (top nav: Research/Explorer, project switcher, theme +
  density toggles, identity/dev-switcher) · Projects list · Project workspace shell +
  tab bar · Chat · Papers · Graph + side panel · LaTeX editor (Split/Editor/Preview/
  Comments) · Explorer · Share modal · Comment thread + @mention.

## 8. Visual design system (locked)

- **Light = Atlas:** `--ground #FCFCFD`, `--surface #FFFFFF`, `--surface-2 #F4F5F8`,
  `--text #0B0F19`, `--muted #5B6172`, `--border #E7E8EE`.
- **Dark = Slate:** `--ground #0E1320`, `--surface #151B2B`, `--surface-2 #1C2438`,
  `--text #E7EAF3`, `--muted #8B93A8`, `--border #242E45`.
- **Accent:** cobalt `#2D3FE0` (light) / `#5566EC` (dark, for contrast).
  **Relevance/positive:** `#0E9F6E` (light) / `#2DD4BF` (dark).
- **Type:** system **sans** (`ui-sans-serif, system-ui…`) for display + body; **serif**
  (`ui-serif, "New York", Georgia…`) on **paper surfaces** — paper titles, abstracts,
  LaTeX preview; **mono** (`ui-monospace…`) for LaTeX source, metadata, data labels.
- **Shape/space:** radius ~12–14px; hairline borders + soft shadow (light) / subtle glow
  (dark); spacious default, compact density option.
- Reference mockup: the approved "Atlas + Manuscript serif" fusion (themes artifact).

## 9. Hard parts & risk mitigation

1. **LaTeX compile (highest infra risk).** Tectonic + baked package cache in a sandboxed
   worker; DB-backed queue (no Redis); resource/time limits; non-root; PDF artifact
   storage. De-risk early with a "hello world → PDF" spike in M5.
2. **Explorer relevance.** Embed papers on ingest (local model, §9.5); relevance =
   cosine(project, paper) blended with keyword overlap → matched/missing chips; cache
   fetched papers. Source APIs differ in fields → the adapter normalizes + tolerates
   gaps (graceful degradation, like the existing searcher).
3. **Async collaboration.** Autosave + version snapshots + permission-gated comments/
   mentions; last-write-wins on body with version history as the safety net. CRDT seam
   preserved (body is plain text, clean PUT boundary).
4. **Multi-tenant correctness.** Permissions tested as a first-class suite; principal
   always from `get_current_user`.
5. **Embeddings provider (open sub-decision → resolved):** local MiniLM/SPECTER2 in the
   worker container (free, no token spend; SPECTER2 paper-tuned). Abstracted behind an
   embeddings client so tests fake it.

## 10. Non-functional & constraints

1. **Single-worker backend** stays load-bearing (in-process event bus + task registry +
   rate-limiter). Compile + (future) realtime live in separate services.
2. **LLM/rate budget** unchanged (Groq free tier; failover via `ProviderPool`). Chat RAG
   adds retrieval but not proportional LLM calls.
3. **Test strategy:** `make test` keeps running on throwaway sqlite with faked LLM +
   faked embedder and **no network**. pgvector is pg-only → similarity goes through an
   abstraction with an in-memory cosine fallback for sqlite; source adapters are faked.
   New: auth-seam tests, permission tests, adapter-normalization tests, compile-job
   state-machine tests (worker mocked).
4. **Prod:** Docker Compose + Caddy unchanged; add the compile worker service. Migrations
   auto-apply at startup. Client-visible errors stay generic (existing policy).

## 11. Phasing & build orchestration

Three tracks, **contract-first**: freeze the OpenAPI/TS types before parallelizing so
frontend and backend build against one shared shape. This thread orchestrates;
subagent-driven-development executes discrete tasks; each task is reviewed before merge.

**Milestones (auth off the critical path):**
- **M0 — Foundation:** design system + both themes + app shell/nav (FE); user/project
  models + `get_current_user` stub + seed + pgvector setup + Alembic (DB/BE); **API
  contract frozen.**
- **M1 — Projects:** projects list/create/workspace shell + members/sharing (BE) + share
  modal + dev user-switcher (FE).
- **M2 — Explorer:** source adapters + relevance engine (BE) + Explorer search UI +
  add/route (FE).
- **M3 — Papers:** project_papers list with relevance/matched-missing + add/remove.
- **M4 — Graph:** edges + connection-analysis (BE) + React Flow graph + side panel (FE).
- **M5 — LaTeX:** documents/versions/comments (BE) + compile worker (Tectonic) +
  CodeMirror/KaTeX/pdf.js editor (FE). Compile spike first.
- **M6 — Chat:** conversations + RAG-over-papers + SSE stream UI.

Ordering is a guide; the implementation plan sequences tasks and checkpoints.

## 12. Future phases (reserved, not built)
- **Auth:** JWT cookie + login/signup; swap `get_current_user` internals; per-user limits.
- **Assistant mode + channels + LinkedIn generator:** new top-level mode; projects feed
  context via a reference pointer (reserved concept).
- **Real-time CRDT:** Yjs/Hocuspocus on the existing document model.

## 13. Acceptance for v1
A user (seeded) can: create a project, invite seeded collaborators with roles, search
Explorer and see per-project relevance, add a paper, view it in Papers + Graph, run a
connection-analysis, ask Chat a grounded question, write LaTeX, compile to a real PDF,
download `.tex`, and comment with an @mention — across light/dark themes — with no login.
