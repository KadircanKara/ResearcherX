# L1: Research Runs ↔ Projects — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bind research runs to projects so the project Chat tab shows past runs and lets users start new ones; a nested route streams the run within the project workspace.

**Architecture:** Add nullable `project_id` FK on `research_runs`; guard `POST /v1/research` with optional membership check when `project_id` is supplied; add `GET /v1/projects/{id}/runs`; wire the frontend Chat tab to list runs and create new ones navigating to a nested `[runId]` route that embeds `RunStream`.

**Tech Stack:** FastAPI, SQLAlchemy 2 async, Alembic, pytest-asyncio, Next.js 15 App Router, TypeScript, Tailwind, shadcn/ui

## Global Constraints

- All DB datetime columns must use `DateTime(timezone=True)` — asyncpg rejects tz-naive datetimes
- `run.status` round-trips as plain `str` from SQLAlchemy — never call `.value`, use `str(run.status)`
- `uvicorn` must stay `--workers 1` — in-process bus and task registry are not multi-worker safe
- `project_id` FK uses `ON DELETE SET NULL` — deleting a project orphans runs, never cascades
- `get_current_user_optional` returns `None` when no `X-Dev-User-Id` header is present (does NOT fall back to the default dev user — that fallback is only in `get_current_user`)
- Anonymous runs (no `project_id`) require no authentication — D3 decision preserved
- Never access lazy-loaded `steps` relationship outside of a selectinload query — construct `RunOut` manually in list endpoints

---

## File Map

**Create:**
- `backend/alembic/versions/<hash>_add_project_id_to_research_runs.py` — migration
- `backend/tests/test_projects_runs.py` — all L1 backend tests
- `frontend/src/app/research/[id]/chat/[runId]/page.tsx` — run stream nested route

**Modify:**
- `backend/app/db/models.py` — add `project_id` to `ResearchRun`
- `backend/app/schemas/research.py` — add `project_id` to `ResearchRequest` and `RunOut`
- `backend/app/core/identity.py` — add `get_current_user_optional`
- `backend/app/services/project_service.py` — add public `require_member` wrapper
- `backend/app/services/research_service.py` — `create()` accepts `project_id`
- `backend/app/api/v1/research.py` — project membership guard in `POST /v1/research`
- `backend/app/api/v1/projects.py` — add `GET /v1/projects/{id}/runs`
- `frontend/src/lib/types.ts` — add `project_id` to `Run`
- `frontend/src/lib/api.ts` — refactor `createRun` to use `apiSend`; add `project_id` param
- `frontend/src/lib/projects.ts` — add `listProjectRuns`
- `frontend/src/app/research/[id]/chat/page.tsx` — run list + inline query form

---

## Task 1: Model + Migration

**Files:**
- Modify: `backend/app/db/models.py`
- Create: `backend/alembic/versions/<hash>_add_project_id_to_research_runs.py`
- Test: `backend/tests/test_projects_runs.py` (first batch)

**Interfaces:**
- Produces: `ResearchRun.project_id: str | None` — used by Tasks 2, 3, 4

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_projects_runs.py`:

```python
"""Tests for research runs ↔ project binding (L1)."""

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Project, ProjectMember, ResearchRun, User
from app.db.seed import seed_users


# ── shared fixtures ──────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def seeded(db_session: AsyncSession):
    await seed_users(db_session)
    await db_session.commit()


@pytest_asyncio.fixture
async def you(db_session: AsyncSession, seeded):
    from sqlalchemy import select
    return (
        await db_session.execute(select(User).where(User.email == "you@researcherx.dev"))
    ).scalar_one()


@pytest_asyncio.fixture
async def amelia(db_session: AsyncSession, seeded):
    from sqlalchemy import select
    return (
        await db_session.execute(select(User).where(User.email == "amelia@lab.io"))
    ).scalar_one()


@pytest_asyncio.fixture
async def project(db_session: AsyncSession, you: User) -> Project:
    p = Project(owner_id=you.id, title="Test Project", topic_keywords=[])
    db_session.add(p)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=p.id, user_id=you.id, role="owner"))
    await db_session.commit()
    await db_session.refresh(p)
    return p


# ── Task 1: model has project_id ─────────────────────────────────────────────


async def test_run_model_has_project_id(db_session: AsyncSession, project: Project):
    run = ResearchRun(question="test question here", project_id=project.id)
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)
    assert run.project_id == project.id


async def test_run_project_id_nullable(db_session: AsyncSession):
    run = ResearchRun(question="anonymous question ok")
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)
    assert run.project_id is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && pytest tests/test_projects_runs.py::test_run_model_has_project_id tests/test_projects_runs.py::test_run_project_id_nullable -v
```

Expected: `FAILED` — `TypeError: ResearchRun() got an unexpected keyword argument 'project_id'`

- [ ] **Step 3: Add `project_id` to `ResearchRun` model**

In `backend/app/db/models.py`, add after the `updated_at` field inside `ResearchRun`:

```python
project_id: Mapped[str | None] = mapped_column(
    ForeignKey("projects.id", ondelete="SET NULL"),
    nullable=True,
    default=None,
    index=True,
)
```

Full `ResearchRun` class after edit:

```python
class ResearchRun(Base):
    __tablename__ = "research_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[RunStatus] = mapped_column(String(16), default=RunStatus.PENDING)
    report: Mapped[str | None] = mapped_column(Text, default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    steps: Mapped[list["AgentStep"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="AgentStep.created_at"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && pytest tests/test_projects_runs.py::test_run_model_has_project_id tests/test_projects_runs.py::test_run_project_id_nullable -v
```

Expected: `2 passed`

- [ ] **Step 5: Generate the Alembic migration**

Run with Docker compose up (`make up` in another terminal if not running):

```bash
docker compose exec backend alembic revision --autogenerate -m "add_project_id_to_research_runs"
```

Open the generated file at `backend/alembic/versions/<hash>_add_project_id_to_research_runs.py`. Verify it contains:

```python
def upgrade() -> None:
    op.add_column(
        "research_runs",
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        op.f("ix_research_runs_project_id"), "research_runs", ["project_id"], unique=False
    )

def downgrade() -> None:
    op.drop_index(op.f("ix_research_runs_project_id"), table_name="research_runs")
    op.drop_column("research_runs", "project_id")
```

If autogenerate missed the FK or index, edit the file to match exactly.

- [ ] **Step 6: Run full test suite to confirm no regressions**

```bash
cd backend && pytest -q
```

Expected: all previous tests pass + 2 new tests pass

- [ ] **Step 7: Commit**

```bash
git add backend/app/db/models.py backend/alembic/versions/ backend/tests/test_projects_runs.py
git commit -m "feat(db): add project_id FK to research_runs + migration"
```

---

## Task 2: Schemas + ResearchService

**Files:**
- Modify: `backend/app/schemas/research.py`
- Modify: `backend/app/services/research_service.py`
- Test: `backend/tests/test_projects_runs.py` (Task 2 section)

**Interfaces:**
- Consumes: `ResearchRun.project_id: str | None` from Task 1
- Produces:
  - `ResearchRequest.project_id: str | None` — read by Task 3 (POST handler)
  - `RunOut.project_id: str | None` — used everywhere RunOut is returned
  - `ResearchService.create(db, question, project_id=None) -> ResearchRun`

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_projects_runs.py`:

```python
# ── Task 2: schemas + service ────────────────────────────────────────────────


async def test_run_out_includes_project_id(db_session: AsyncSession, project: Project):
    from app.schemas.research import RunOut
    run = ResearchRun(question="schema test question", project_id=project.id)
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)
    out = RunOut(
        id=run.id,
        question=run.question,
        status=str(run.status),
        report=run.report,
        error=run.error,
        project_id=run.project_id,
        created_at=run.created_at,
        steps=[],
    )
    assert out.project_id == project.id


async def test_service_create_with_project_id(db_session: AsyncSession, project: Project):
    from app.services.research_service import ResearchService
    svc = ResearchService()
    run = await svc.create(db_session, "what is machine learning here", project.id)
    assert run.project_id == project.id


async def test_service_create_without_project_id(db_session: AsyncSession):
    from app.services.research_service import ResearchService
    svc = ResearchService()
    run = await svc.create(db_session, "what is machine learning here")
    assert run.project_id is None
```

- [ ] **Step 2: Run to verify failure**

```bash
cd backend && pytest tests/test_projects_runs.py::test_run_out_includes_project_id tests/test_projects_runs.py::test_service_create_with_project_id tests/test_projects_runs.py::test_service_create_without_project_id -v
```

Expected: first test passes (RunOut construction works with the new field), last two fail — `ResearchService.create()` doesn't accept `project_id` yet.

- [ ] **Step 3: Update `ResearchRequest` and `RunOut` schemas**

In `backend/app/schemas/research.py`, update:

```python
class ResearchRequest(BaseModel):
    question: str = Field(min_length=5, max_length=1000)
    project_id: str | None = None


class RunOut(BaseModel):
    id: str
    question: str
    status: str
    report: str | None
    error: str | None
    project_id: str | None
    created_at: datetime
    steps: list[StepOut] = []

    model_config = {"from_attributes": True}
```

- [ ] **Step 4: Update `ResearchService.create()`**

In `backend/app/services/research_service.py`, update the `create` method:

```python
async def create(
    self, db: AsyncSession, question: str, project_id: str | None = None
) -> ResearchRun:
    run = ResearchRun(question=question, project_id=project_id)
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run
```

- [ ] **Step 5: Run tests**

```bash
cd backend && pytest tests/test_projects_runs.py::test_run_out_includes_project_id tests/test_projects_runs.py::test_service_create_with_project_id tests/test_projects_runs.py::test_service_create_without_project_id -v
```

Expected: `3 passed`

- [ ] **Step 6: Run full suite**

```bash
cd backend && pytest -q
```

Expected: all pass (RunOut now has `project_id` — verify existing serialisation tests still pass)

- [ ] **Step 7: Commit**

```bash
git add backend/app/schemas/research.py backend/app/services/research_service.py backend/tests/test_projects_runs.py
git commit -m "feat(research): add project_id to ResearchRequest, RunOut, and service.create"
```

---

## Task 3: Identity Optional + POST /research Project Guard

**Files:**
- Modify: `backend/app/core/identity.py`
- Modify: `backend/app/services/project_service.py`
- Modify: `backend/app/api/v1/research.py`
- Test: `backend/tests/test_projects_runs.py` (Task 3 section)

**Interfaces:**
- Consumes: `ResearchRequest.project_id`, `ResearchService.create(project_id=)` from Task 2
- Produces:
  - `get_current_user_optional(request, db) -> User | None` — imported by research.py
  - `project_service.require_member(db, project_id, user_id, need) -> ProjectMember` — imported by research.py

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_projects_runs.py`:

```python
# ── Task 3: POST /research project guard ─────────────────────────────────────


async def test_create_run_with_project_id_as_member(client, you: User, project: Project):
    r = await client.post(
        "/v1/research",
        json={"question": "what is quantum computing", "project_id": project.id},
        headers={"X-Dev-User-Id": you.id},
    )
    assert r.status_code == 201
    data = r.json()
    assert data["project_id"] == project.id


async def test_create_run_without_project_id_is_anonymous(client):
    r = await client.post(
        "/v1/research",
        json={"question": "what is quantum computing"},
    )
    assert r.status_code == 201
    assert r.json()["project_id"] is None


async def test_create_run_with_project_id_no_identity(client, project: Project):
    # No X-Dev-User-Id header → get_current_user_optional returns None → 401
    r = await client.post(
        "/v1/research",
        json={"question": "what is quantum computing", "project_id": project.id},
    )
    assert r.status_code == 401


async def test_create_run_with_project_id_non_member(client, amelia: User, project: Project):
    # amelia is not in the project
    r = await client.post(
        "/v1/research",
        json={"question": "what is quantum computing", "project_id": project.id},
        headers={"X-Dev-User-Id": amelia.id},
    )
    assert r.status_code == 404
```

- [ ] **Step 2: Run to verify failure**

```bash
cd backend && pytest tests/test_projects_runs.py::test_create_run_with_project_id_as_member tests/test_projects_runs.py::test_create_run_without_project_id_is_anonymous tests/test_projects_runs.py::test_create_run_with_project_id_no_identity tests/test_projects_runs.py::test_create_run_with_project_id_non_member -v
```

Expected: `test_create_run_without_project_id_is_anonymous` passes (anonymous still works), rest fail

- [ ] **Step 3: Add `get_current_user_optional` to identity.py**

In `backend/app/core/identity.py`, add after `get_current_user`:

```python
async def get_current_user_optional(
    request: Request, db: AsyncSession = Depends(get_session)
) -> User | None:
    """Resolve identity only when an explicit identity header is present.

    Returns None when no identity is provided — callers decide whether to
    require it. Does NOT fall back to the default dev user; that fallback
    is only in get_current_user.
    """
    if settings.environment == "dev":
        dev_id = request.headers.get("X-Dev-User-Id")
        if dev_id:
            user = await db.get(User, dev_id)
            if user is not None:
                return user
    return None
```

- [ ] **Step 4: Add public `require_member` to project_service.py**

In `backend/app/services/project_service.py`, add after `_require_member`:

```python
async def require_member(
    db: AsyncSession, project_id: str, user_id: str, need: str = "viewer"
) -> ProjectMember:
    """Public wrapper around _require_member for use outside this module."""
    return await _require_member(db, project_id, user_id, need)
```

- [ ] **Step 5: Update `POST /v1/research` handler**

Replace `backend/app/api/v1/research.py` `create_run` function and its imports:

```python
import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.core.identity import get_current_user_optional
from app.core.logging import log
from app.core.security import enforce_read_limits, enforce_run_quotas
from app.db.models import User
from app.db.session import get_session
from app.schemas.research import ResearchRequest, RunOut
from app.services import project_service
from app.services.event_bus import bus
from app.services.research_service import ResearchService
from app.services.task_registry import registry

router = APIRouter(prefix="/research", tags=["research"])

service = ResearchService()

UNWATCHED_CANCEL_GRACE_S = 10.0
_watchers: set[asyncio.Task] = set()


def _watch_unwatched(run_id: str) -> None:
    async def _cancel_if_still_unwatched() -> None:
        await asyncio.sleep(UNWATCHED_CANCEL_GRACE_S)
        if bus.subscriber_count(run_id) == 0 and registry.cancel(run_id):
            log.info("run_cancelled_no_viewers", run_id=run_id)

    watcher = asyncio.create_task(_cancel_if_still_unwatched())
    _watchers.add(watcher)
    watcher.add_done_callback(_watchers.discard)


@router.post(
    "",
    response_model=RunOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(enforce_run_quotas)],
)
async def create_run(
    payload: ResearchRequest,
    db: AsyncSession = Depends(get_session),
    user: User | None = Depends(get_current_user_optional),
) -> RunOut:
    if payload.project_id is not None:
        if user is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        await project_service.require_member(db, payload.project_id, user.id, "viewer")

    run = await service.create(db, payload.question, payload.project_id)
    registry.register(run.id, asyncio.create_task(service.run_async(run.id)))
    return RunOut(
        id=run.id,
        question=run.question,
        status=str(run.status),
        report=run.report,
        error=run.error,
        project_id=run.project_id,
        created_at=run.created_at,
        steps=[],
    )


@router.get("/{run_id}", response_model=RunOut, dependencies=[Depends(enforce_read_limits)])
async def get_run(run_id: str, db: AsyncSession = Depends(get_session)) -> RunOut:
    run = await service.get(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return RunOut.model_validate(run)


@router.get("/{run_id}/events", dependencies=[Depends(enforce_read_limits)])
async def stream_events(run_id: str, request: Request) -> EventSourceResponse:
    queue = bus.subscribe(run_id)

    async def event_gen() -> AsyncIterator[dict]:
        try:
            while True:
                if await request.is_disconnected():
                    break
                event = await queue.get()
                if event is None:
                    yield {"event": "end", "data": "{}"}
                    break
                yield {"event": event.get("type", "message"), "data": json.dumps(event)}
        finally:
            bus.unsubscribe(run_id, queue)
            if bus.subscriber_count(run_id) == 0 and registry.get(run_id) is not None:
                _watch_unwatched(run_id)

    return EventSourceResponse(event_gen())
```

- [ ] **Step 6: Run Task 3 tests**

```bash
cd backend && pytest tests/test_projects_runs.py::test_create_run_with_project_id_as_member tests/test_projects_runs.py::test_create_run_without_project_id_is_anonymous tests/test_projects_runs.py::test_create_run_with_project_id_no_identity tests/test_projects_runs.py::test_create_run_with_project_id_non_member -v
```

Expected: `4 passed`

- [ ] **Step 7: Run full suite**

```bash
cd backend && pytest -q
```

Expected: all pass

- [ ] **Step 8: Commit**

```bash
git add backend/app/core/identity.py backend/app/services/project_service.py backend/app/api/v1/research.py backend/tests/test_projects_runs.py
git commit -m "feat(research): guard POST /research with optional project membership check"
```

---

## Task 4: GET /v1/projects/{id}/runs

**Files:**
- Modify: `backend/app/api/v1/projects.py`
- Test: `backend/tests/test_projects_runs.py` (Task 4 section)

**Interfaces:**
- Consumes: `project_service.require_member`, `ResearchRun.project_id`, `RunOut` from prior tasks
- Produces: `GET /v1/projects/{project_id}/runs?limit=20&offset=0` → `list[RunOut]`

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_projects_runs.py`:

```python
# ── Task 4: GET /v1/projects/{id}/runs ───────────────────────────────────────


async def test_list_project_runs(client, you: User, project: Project, db_session: AsyncSession):
    from datetime import timedelta, timezone
    from datetime import datetime as dt

    base = dt.now(timezone.utc)
    db_session.add(ResearchRun(
        question="older question here",
        project_id=project.id,
        created_at=base - timedelta(hours=1),
    ))
    db_session.add(ResearchRun(
        question="newer question here",
        project_id=project.id,
        created_at=base,
    ))
    await db_session.commit()

    r = await client.get(
        f"/v1/projects/{project.id}/runs",
        headers={"X-Dev-User-Id": you.id},
    )
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2
    assert data[0]["question"] == "newer question here"
    assert data[1]["question"] == "older question here"
    assert data[0]["project_id"] == project.id


async def test_list_project_runs_excludes_other_projects(
    client, you: User, project: Project, db_session: AsyncSession
):
    other = Project(owner_id=you.id, title="Other", topic_keywords=[])
    db_session.add(other)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=other.id, user_id=you.id, role="owner"))
    db_session.add(ResearchRun(question="belongs to other project", project_id=other.id))
    db_session.add(ResearchRun(question="belongs to this project", project_id=project.id))
    await db_session.commit()

    r = await client.get(
        f"/v1/projects/{project.id}/runs",
        headers={"X-Dev-User-Id": you.id},
    )
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["question"] == "belongs to this project"


async def test_list_project_runs_non_member(client, amelia: User, project: Project):
    r = await client.get(
        f"/v1/projects/{project.id}/runs",
        headers={"X-Dev-User-Id": amelia.id},
    )
    assert r.status_code == 404


async def test_viewer_can_list_runs(client, amelia: User, project: Project, db_session: AsyncSession):
    db_session.add(ProjectMember(project_id=project.id, user_id=amelia.id, role="viewer"))
    db_session.add(ResearchRun(question="viewer can see this run", project_id=project.id))
    await db_session.commit()

    r = await client.get(
        f"/v1/projects/{project.id}/runs",
        headers={"X-Dev-User-Id": amelia.id},
    )
    assert r.status_code == 200
    assert len(r.json()) == 1
```

- [ ] **Step 2: Run to verify failure**

```bash
cd backend && pytest tests/test_projects_runs.py::test_list_project_runs tests/test_projects_runs.py::test_list_project_runs_excludes_other_projects tests/test_projects_runs.py::test_list_project_runs_non_member tests/test_projects_runs.py::test_viewer_can_list_runs -v
```

Expected: `4 failed` — endpoint doesn't exist yet

- [ ] **Step 3: Add `GET /projects/{id}/runs` to projects router**

In `backend/app/api/v1/projects.py`, add these imports at the top of the file (after existing imports):

```python
from sqlalchemy import desc, select as sa_select

from app.db.models import ResearchRun
from app.schemas.research import RunOut
```

Then add this endpoint after the `delete_project` handler (before the `# ── members ──` block):

```python
@router.get("/projects/{project_id}/runs", response_model=list[RunOut])
async def list_project_runs(
    project_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> list[RunOut]:
    await project_service.require_member(db, project_id, user.id, "viewer")
    result = await db.execute(
        sa_select(ResearchRun)
        .where(ResearchRun.project_id == project_id)
        .order_by(desc(ResearchRun.created_at))
        .limit(limit)
        .offset(offset)
    )
    runs = result.scalars().all()
    return [
        RunOut(
            id=r.id,
            question=r.question,
            status=str(r.status),
            report=r.report,
            error=r.error,
            project_id=r.project_id,
            created_at=r.created_at,
            steps=[],
        )
        for r in runs
    ]
```

Also add `Query` to the FastAPI imports at the top of `projects.py`:

```python
from fastapi import APIRouter, Depends, Query, Response
```

- [ ] **Step 4: Run Task 4 tests**

```bash
cd backend && pytest tests/test_projects_runs.py::test_list_project_runs tests/test_projects_runs.py::test_list_project_runs_excludes_other_projects tests/test_projects_runs.py::test_list_project_runs_non_member tests/test_projects_runs.py::test_viewer_can_list_runs -v
```

Expected: `4 passed`

- [ ] **Step 5: Run full suite + ruff**

```bash
cd backend && pytest -q && ruff check . && ruff format --check .
```

Expected: all pass, no lint errors

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/v1/projects.py backend/tests/test_projects_runs.py
git commit -m "feat(projects): GET /projects/{id}/runs endpoint"
```

---

## Task 5: Frontend — Types + API Client

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/lib/projects.ts`

**Interfaces:**
- Produces:
  - `Run.project_id: string | null`
  - `createRun(question: string, projectId?: string): Promise<Run>`
  - `listProjectRuns(projectId: string, limit?: number, offset?: number): Promise<Run[]>`

- [ ] **Step 1: Add `project_id` to the `Run` type**

In `frontend/src/lib/types.ts`, update the `Run` interface:

```typescript
export interface Run {
  id: string;
  question: string;
  status: RunStatus;
  report: string | null;
  error: string | null;
  project_id: string | null;
  created_at: string;
  steps: Step[];
}
```

- [ ] **Step 2: Refactor `createRun` and add `listProjectRuns`**

In `frontend/src/lib/api.ts`, replace `createRun`:

```typescript
export async function createRun(question: string, projectId?: string): Promise<Run> {
  return (await apiSend<Run>("POST", "/research", {
    question,
    project_id: projectId ?? null,
  })) as Run;
}
```

Keep `getRun` and `eventsUrl` unchanged.

In `frontend/src/lib/projects.ts`, add at the end of the file:

```typescript
export async function listProjectRuns(
  projectId: string,
  limit = 20,
  offset = 0,
): Promise<Run[]> {
  return apiGet<Run[]>(`/projects/${projectId}/runs?limit=${limit}&offset=${offset}`);
}
```

Add `Run` to the import from `./types` at the top of `projects.ts`:

```typescript
import type { Project, ProjectDetail, Member, Role, Run } from "./types";
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
cd frontend && npm run typecheck
```

Expected: no errors

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/lib/api.ts frontend/src/lib/projects.ts
git commit -m "feat(frontend): add project_id to Run type; refactor createRun; add listProjectRuns"
```

---

## Task 6: Frontend — Chat Page (Run List + Query Form)

**Files:**
- Modify: `frontend/src/app/research/[id]/chat/page.tsx`

**Interfaces:**
- Consumes: `listProjectRuns`, `createRun`, `Run` from Task 5
- Produces: Chat page renders run list + inline form; navigates to `/research/[id]/chat/[runId]` on submit

- [ ] **Step 1: Replace chat page**

Replace the entire contents of `frontend/src/app/research/[id]/chat/page.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { ArrowRight, Loader2, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { createRun } from "@/lib/api";
import { listProjectRuns } from "@/lib/projects";
import type { Run } from "@/lib/types";

const STATUS_CONFIG = {
  pending:   { label: "Pending",   cls: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400" },
  running:   { label: "Running",   cls: "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400" },
  completed: { label: "Done",      cls: "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400" },
  failed:    { label: "Failed",    cls: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400" },
} as const;

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

export default function ChatPage() {
  const { id: projectId } = useParams<{ id: string }>();
  const router = useRouter();

  const [runs, setRuns] = useState<Run[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [question, setQuestion] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  useEffect(() => {
    listProjectRuns(projectId)
      .then(setRuns)
      .catch(() => {
        // layout.tsx handles 404/403 for the project itself
      })
      .finally(() => setLoading(false));
  }, [projectId]);

  async function handleSubmit() {
    const q = question.trim();
    if (!q || submitting) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const run = await createRun(q, projectId);
      router.push(`/research/${projectId}/chat/${run.id}`);
    } catch {
      setSubmitError("Failed to start research. Please try again.");
      setSubmitting(false);
    }
  }

  if (loading) {
    return (
      <div className="space-y-3 py-4">
        {[1, 2, 3].map((i) => (
          <div key={i} className="h-16 animate-pulse rounded-xl bg-muted" />
        ))}
      </div>
    );
  }

  return (
    <div>
      {/* Header row */}
      <div className="mb-4 flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          {runs.length === 0
            ? "No research yet"
            : `${runs.length} run${runs.length !== 1 ? "s" : ""}`}
        </p>
        {!showForm && (
          <Button size="sm" onClick={() => setShowForm(true)}>
            <Plus className="mr-1.5 size-3.5" />
            New Research
          </Button>
        )}
      </div>

      {/* Inline query form */}
      {showForm && (
        <div className="mb-4 rounded-xl border border-border bg-card p-4">
          <textarea
            autoFocus
            rows={3}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                handleSubmit();
              }
            }}
            placeholder="What do you want to research?"
            className="w-full resize-none bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          />
          {submitError && (
            <p className="mt-1 text-xs text-destructive">{submitError}</p>
          )}
          <div className="mt-3 flex gap-2">
            <Button
              size="sm"
              onClick={handleSubmit}
              disabled={!question.trim() || submitting}
            >
              {submitting ? (
                <Loader2 className="mr-1.5 size-3.5 animate-spin" />
              ) : (
                <ArrowRight className="mr-1.5 size-3.5" />
              )}
              Research
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setShowForm(false);
                setQuestion("");
                setSubmitError(null);
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}

      {/* Empty state */}
      {runs.length === 0 && !showForm && (
        <div className="flex flex-col items-center gap-2 py-24 text-center">
          <p className="text-sm text-muted-foreground">
            Start your first research run to see results here.
          </p>
        </div>
      )}

      {/* Run list */}
      <div className="space-y-2">
        {runs.map((run) => {
          const sc = STATUS_CONFIG[run.status] ?? STATUS_CONFIG.pending;
          return (
            <button
              key={run.id}
              type="button"
              onClick={() =>
                router.push(`/research/${projectId}/chat/${run.id}`)
              }
              className="group flex w-full items-start gap-3 rounded-xl border border-border bg-card px-4 py-3 text-left transition-colors hover:bg-muted"
            >
              <div className="min-w-0 flex-1">
                <p className="line-clamp-2 text-sm font-medium text-foreground">
                  {run.question}
                </p>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {fmtDate(run.created_at)}
                </p>
              </div>
              <span
                className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ${sc.cls}`}
              >
                {sc.label}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd frontend && npm run typecheck
```

Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/app/research/\[id\]/chat/page.tsx
git commit -m "feat(ui): project Chat tab — run list + inline new research form"
```

---

## Task 7: Frontend — Run Stream Nested Route

**Files:**
- Create: `frontend/src/app/research/[id]/chat/[runId]/page.tsx`

**Interfaces:**
- Consumes: `RunStream` component (already exists at `components/run-stream.tsx`)
- Produces: `/research/[id]/chat/[runId]` renders run stream inside project workspace

- [ ] **Step 1: Create the run stream page**

Create `frontend/src/app/research/[id]/chat/[runId]/page.tsx`:

```tsx
"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { RunStream } from "@/components/run-stream";

export default function RunPage() {
  const { id: projectId, runId } = useParams<{ id: string; runId: string }>();

  return (
    <div>
      <Link
        href={`/research/${projectId}/chat`}
        className="mb-4 inline-flex items-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground"
      >
        <ArrowLeft className="size-3.5" />
        All research
      </Link>
      <RunStream runId={runId} />
    </div>
  );
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd frontend && npm run typecheck
```

Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add "frontend/src/app/research/[id]/chat/[runId]/page.tsx"
git commit -m "feat(ui): run stream nested route within project workspace"
```

---

## Task 8: End-to-End Smoke Test + PR

- [ ] **Step 1: Run full backend test suite**

```bash
cd backend && pytest -q
```

Expected: all tests pass (88 existing + ~12 new = ~100 total)

- [ ] **Step 2: Run frontend checks**

```bash
cd frontend && npm run typecheck && npm run lint && npm run build
```

Expected: no errors, build succeeds

- [ ] **Step 3: Start dev stack and smoke test manually**

```bash
# From repo root
make up
```

Open `http://localhost:3000`.

Verify the full flow:
1. Open a project → Chat tab shows "No research yet" + "New Research" button
2. Click "New Research" → form appears with textarea
3. Type "what is quantum computing" (≥5 chars) → click Research (or press Enter)
4. Browser navigates to `/research/[projectId]/chat/[runId]`
5. RunStream renders — events stream in, plan/findings/report appear
6. "All research" back link visible → clicking it returns to the run list
7. Run list now shows the completed run with status badge and date

- [ ] **Step 4: Push branch and open PR**

```bash
git push -u origin feat/l1-runs-projects
gh pr create \
  --base main \
  --title "feat(L1): research runs ↔ projects — bind runs to projects, wire Chat tab" \
  --body "$(cat <<'EOF'
## Summary

- Adds nullable `project_id` FK to `research_runs` with Alembic migration
- `POST /v1/research` accepts optional `project_id`; verifies viewer+ membership when supplied; anonymous runs unchanged
- `GET /v1/projects/{id}/runs` lists a project's runs (desc order, paginated)
- New `get_current_user_optional` identity dependency (returns None when no header present)
- Project Chat tab: run list with status badges + inline "New Research" form
- Nested route `/research/[id]/chat/[runId]` renders RunStream inside project workspace

## Test plan

- [ ] `make test` → all tests pass
- [ ] CI green
- [ ] Manual: create project → Chat tab → New Research → run streams → back to list → run appears

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 5: Merge after CI is green**

```bash
gh pr merge --merge
```
