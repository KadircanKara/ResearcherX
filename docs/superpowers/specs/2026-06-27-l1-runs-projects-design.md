# L1: Research Runs ↔ Projects — Design Spec

**Branch:** `feat/l1-runs-projects`  
**Date:** 2026-06-27  
**Phase:** Local L1 (see HANDOFF.md)

---

## Goal

Bind research runs to projects. The project Chat tab becomes the research interface: a list of past runs + a "New Research" form. Starting a run navigates to a stream view that lives inside the project workspace (header + tabs stay visible).

---

## Backend

### 1. Model

Add `project_id` to `ResearchRun`:

```python
project_id: Mapped[str | None] = mapped_column(
    ForeignKey("projects.id", ondelete="SET NULL"),
    nullable=True,
    default=None,
    index=True,
)
```

Nullable — anonymous runs (no project context) and existing rows remain valid. `ON DELETE SET NULL` so deleting a project orphans its runs rather than cascading deletes (research data is preserved).

### 2. Migration

New Alembic revision:
- `ADD COLUMN project_id VARCHAR(36) NULL REFERENCES projects(id) ON DELETE SET NULL`
- `CREATE INDEX ix_research_runs_project_id ON research_runs(project_id)`

### 3. Schemas

**`ResearchRequest`** — add:
```python
project_id: str | None = None
```

**`RunOut`** — add:
```python
project_id: str | None
```

### 4. Identity dependency

New optional dependency in `core/identity.py`:

```python
async def get_current_user_optional(request, db) -> User | None
```

Returns `User | None` — resolves identity if `X-Dev-User-Id` header present, else `None`. Used by `POST /v1/research` so anonymous runs keep working when no `project_id` is given.

### 5. `POST /v1/research`

When `project_id` is provided:
1. Resolve `get_current_user_optional` — if no user identity, raise 401.
2. Call `project_service._require_member(db, project_id, user.id, "viewer")` — raises 404 (non-member) or 403 (under-ranked).
3. Pass `project_id` to `service.create()`.

When `project_id` is omitted: anonymous path unchanged — no identity required, no project check.

### 6. `ResearchService.create()`

```python
async def create(self, db, question: str, project_id: str | None = None) -> ResearchRun
```

### 7. New endpoint: `GET /v1/projects/{id}/runs`

Added to `api/v1/projects.py`:
- Auth: `get_current_user` + viewer+ membership check (reuses `_require_member`).
- Query: `SELECT * FROM research_runs WHERE project_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?`
- Query params: `limit: int = 20`, `offset: int = 0`
- Response: `list[RunOut]`

---

## Frontend

### 1. Types (`lib/types.ts`)

Add to `Run`:
```typescript
project_id: string | null;
```

### 2. API client (`lib/api.ts`)

```typescript
export async function createRun(question: string, projectId?: string): Promise<Run>
```

Passes `project_id` in the JSON body when provided. Sends `X-Dev-User-Id` header via `apiSend` (already wired through `devUserId`).

### 3. Projects client (`lib/projects.ts`)

```typescript
export async function listProjectRuns(projectId: string, limit = 20, offset = 0): Promise<Run[]>
```

Calls `GET /v1/projects/{id}/runs?limit=...&offset=...`.

### 4. Chat page (`/research/[id]/chat/page.tsx`)

**States:**
- Loading skeleton
- Empty state: "No research yet" + "New Research" CTA
- Run list: cards showing question (truncated), status badge (colour-coded), relative created_at
- Inline query form (appears when "New Research" clicked): textarea + submit button; closes on cancel or submit

**On submit:**
1. `createRun(question, projectId)`
2. Navigate to `/research/[id]/chat/[runId]`

Run list polls or is fetched once on mount (no SSE needed here — individual run streams handle live state).

### 5. Run stream page (`/research/[id]/chat/[runId]/page.tsx`)

```
Client component.
Renders <RunStream runId={runId} />
Back link → /research/[id]/chat
```

The existing project layout (`layout.tsx`) wraps this automatically — header + tabs visible.

---

## Tests (`backend/tests/test_projects_runs.py`)

| # | Scenario | Expected |
|---|---|---|
| 1 | POST /research with valid `project_id` as member | 201, response has `project_id` |
| 2 | POST /research without `project_id` | 201, `project_id` is null |
| 3 | POST /research with `project_id`, no identity header | 401 |
| 4 | POST /research with `project_id`, user is not a member | 404 |
| 5 | GET /projects/{id}/runs as member | 200, only this project's runs, desc order |
| 6 | GET /projects/{id}/runs as non-member | 404 |
| 7 | Viewer role can POST and GET runs | 201 / 200 |

---

## Out of Scope (L1)

- Breadcrumb path in project header (polish)
- Pagination UI in the run list (backend supports it; frontend uses default limit=20)
- Run list auto-refresh / real-time status updates (SSE stream page handles live state)
- Detaching a run from a project
