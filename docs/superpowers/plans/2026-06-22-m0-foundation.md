# M0 — Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The app boots with a stubbed identity layer (seeded users + dev "act as" switch) and a themed Next.js shell (Atlas light / Slate dark), ready for feature milestones to build against a frozen `User` contract.

**Architecture:** Evolve the existing FastAPI + SQLAlchemy 2.0 backend — add a `users` table, a `get_current_user` dependency that resolves a *seeded* principal while auth is deferred, an idempotent seed, and `/v1/me` + `/v1/users` endpoints. Rebuild the Next.js frontend foundation on shadcn/ui with two CSS-variable themes and an app shell. Auth (login/JWT) is deferred; identity is not — the whole seam is one dependency.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 (async), Alembic, pytest, httpx; Next.js 15 App Router, React 19, TypeScript, Tailwind 3.4, shadcn/ui, next-themes.

## Global Constraints

- **Branch:** `feat/research-hub-rebuild` (already created). Commit after each task.
- **Backend tests run on the host venv** (sqlite + offline, no Docker): `cd backend && ./.venv/bin/pytest …`. The venv is installed; the existing 60-test suite passes there. Do NOT require Docker or network in tests.
- **API prefix is `/v1`** (from `app/api/v1/router.py`: `api_router = APIRouter(prefix="/v1")`). Final identity paths: `/v1/me`, `/v1/users`. Routers are included on `api_router` and add their own sub-prefix or none.
- **Session maker is `SessionLocal`** (`app/db/session.py`); request DI is `get_session`.
- IDs are `String(36)` UUIDs via `_uuid()`; status/enum columns are `StrEnum` over `String(16)`; **all** datetimes `DateTime(timezone=True)` via `_now()`. List fields use `JSON` (sqlite-portable). pgvector/embeddings are **not** in M0.
- `run.status`-style columns return plain `str` after round-trip — never call `.value`.
- Backend runs **single-worker** — no multi-worker assumptions.
- Frontend: **no server-side fetches** to the backend (inside the container `localhost` is the frontend). Client components fetch. The client already exports `API_BASE` (`process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000"`) and uses `/v1/...`. Never add `rehype-raw` to any markdown renderer.
- Visual tokens are fixed (Task F1). Accent cobalt `#2D3FE0` (light) / `#5566EC` (dark). Serif only on paper surfaces.

**Contract note (refinement of spec §11):** we freeze the API contract **per milestone**. M0 freezes the identity contract below; later milestones freeze their own resources.

### Frozen contract — Identity (M0)
```
GET  /v1/me      -> 200 UserOut
GET  /v1/users   -> 200 UserOut[]            (teammate picker; seeded in v1)
Header (dev only): X-Dev-User-Id: <user uuid>   (honored iff ENVIRONMENT=dev)

UserOut = { id: string, email: string, name: string, avatar_color: string }
```

---

## Task B0: Shared backend test fixtures (`db_session`, `client`)

**Files:**
- Modify: `backend/tests/conftest.py` (append two fixtures)

**Interfaces:**
- Produces: pytest fixtures `db_session` (an `AsyncSession` on the test engine) and `client` (an `httpx.AsyncClient` over the ASGI `app`). The autouse `fresh_db` fixture already recreates the schema per test.

- [ ] **Step 1: Append fixtures to `conftest.py`**

```python
# backend/tests/conftest.py  (append)
import httpx  # noqa: E402
import pytest_asyncio  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402


@pytest_asyncio.fixture
async def db_session():
    async with SessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
```
> ASGITransport does not run lifespan, so startup seeding will not fire under `client` — tests seed explicitly (Task B2). `db_session` and the app's `get_session` share one engine/sqlite file, so committed rows are visible across both.

- [ ] **Step 2: Sanity-check the fixtures load**

```python
# (temporary) at bottom of conftest is fine to verify, then remove:
```
Run: `cd backend && ./.venv/bin/pytest -q`
Expected: still 60 passed (fixtures unused yet, must not break collection).

- [ ] **Step 3: Commit**

```bash
git add backend/tests/conftest.py
git commit -m "test: shared db_session + client fixtures"
```

---

## Task B1: `User` model + migration

**Files:**
- Modify: `backend/app/db/models.py` (append `User`)
- Create: `backend/alembic/versions/<rev>_add_users.py`
- Test: `backend/tests/test_users_model.py`

**Interfaces:**
- Consumes: `db_session` (B0).
- Produces: `User` ORM class — fields `id, email, name, password_hash (nullable), avatar_color, created_at`.

- [ ] **Step 1: Failing test**

```python
# backend/tests/test_users_model.py
from sqlalchemy import select
from app.db.models import User


async def test_user_persists_with_defaults(db_session):
    db_session.add(User(email="a@x.io", name="A"))
    await db_session.commit()
    got = (await db_session.execute(select(User).where(User.email == "a@x.io"))).scalar_one()
    assert got.name == "A"
    assert got.avatar_color == "#2D3FE0"   # default
    assert got.password_hash is None        # auth deferred
    assert len(got.id) == 36
```

- [ ] **Step 2: Run, verify fail** — `cd backend && ./.venv/bin/pytest tests/test_users_model.py -v` → FAIL (`cannot import name 'User'`).

- [ ] **Step 3: Implement the model**

```python
# backend/app/db/models.py  (append; reuse existing _uuid/_now/String/DateTime imports)
class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    # Auth deferred: nullable now so the auth phase is purely additive.
    password_hash: Mapped[str | None] = mapped_column(String(255), default=None)
    avatar_color: Mapped[str] = mapped_column(String(9), default="#2D3FE0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
```

- [ ] **Step 4: Run, verify pass** — same command → PASS.

- [ ] **Step 5: Create the migration**

Autogenerate against a throwaway local sqlite (host, no Docker):
```bash
cd backend
DATABASE_URL=sqlite+aiosqlite:///./_mig.db ./.venv/bin/alembic upgrade head
DATABASE_URL=sqlite+aiosqlite:///./_mig.db ./.venv/bin/alembic revision --autogenerate -m "add users table"
rm -f ./_mig.db
```
Open the generated file in `alembic/versions/`. Confirm it **only** creates `users` (columns + unique index on `email`), matching the style of `9185a9a789e6_init.py`. If autogenerate emits anything unrelated, hand-edit it down to just the users table (model it on the init migration). `down_revision` must point at the current head.

- [ ] **Step 6: Commit**

```bash
git add backend/app/db/models.py backend/alembic/versions/ backend/tests/test_users_model.py
git commit -m "feat(db): add User model + migration"
```

---

## Task B2: Identity seam — `get_current_user`, `UserOut`, `/v1/me` + `/v1/users`

**Files:**
- Create: `backend/app/core/identity.py`, `backend/app/api/v1/users.py`, `backend/app/schemas/user.py`
- Modify: `backend/app/api/v1/router.py`
- Test: `backend/tests/test_identity.py`

**Interfaces:**
- Consumes: `User` (B1); `get_session`; `settings`; `client`/`db_session` (B0); `seed_users` (B3 — tests seed inline).
- Produces: `get_current_user(request, db) -> User`; `UserOut {id,email,name,avatar_color}`; routes `GET /v1/me`, `GET /v1/users`; constant `DEFAULT_USER_EMAIL = "you@researcherx.dev"`.

- [ ] **Step 1: Failing tests**

```python
# backend/tests/test_identity.py
from sqlalchemy import select
import pytest_asyncio
from app.db.models import User
from app.db.seed import seed_users
from app.core.identity import DEFAULT_USER_EMAIL


@pytest_asyncio.fixture
async def seeded(db_session):
    await seed_users(db_session)
    await db_session.commit()


async def test_me_defaults_to_you(client, seeded):
    r = await client.get("/v1/me")
    assert r.status_code == 200
    assert r.json()["email"] == DEFAULT_USER_EMAIL


async def test_dev_header_switches_principal(client, seeded, db_session):
    amelia = (await db_session.execute(select(User).where(User.email == "amelia@lab.io"))).scalar_one()
    r = await client.get("/v1/me", headers={"X-Dev-User-Id": amelia.id})
    assert r.json()["email"] == "amelia@lab.io"


async def test_dev_header_ignored_outside_dev(client, seeded, db_session, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "environment", "prod")
    amelia = (await db_session.execute(select(User).where(User.email == "amelia@lab.io"))).scalar_one()
    r = await client.get("/v1/me", headers={"X-Dev-User-Id": amelia.id})
    assert r.json()["email"] == DEFAULT_USER_EMAIL   # header ignored


async def test_list_users_returns_seeds(client, seeded):
    r = await client.get("/v1/users")
    assert r.status_code == 200
    assert {"you@researcherx.dev", "amelia@lab.io", "marco@lab.io"} <= {u["email"] for u in r.json()}
```

- [ ] **Step 2: Run, verify fail** — `cd backend && ./.venv/bin/pytest tests/test_identity.py -v` → FAIL (imports). (Depends on Task B3's `seed_users`; if running B2 before B3, expect the import error to name `seed`.)

- [ ] **Step 3: Implement**

```python
# backend/app/schemas/user.py
from pydantic import BaseModel


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    avatar_color: str
    model_config = {"from_attributes": True}
```

```python
# backend/app/core/identity.py
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import User
from app.db.session import get_session

DEFAULT_USER_EMAIL = "you@researcherx.dev"


async def get_current_user(request: Request, db: AsyncSession = Depends(get_session)) -> User:
    """Resolve the acting principal. Auth is deferred.

    In dev, an X-Dev-User-Id header lets the client act as a seeded teammate so
    collaboration features are exercisable without login. The auth phase replaces
    ONLY this body (validate a JWT cookie); callers and signature are unchanged.
    """
    if settings.environment == "dev":
        dev_id = request.headers.get("X-Dev-User-Id")
        if dev_id:
            user = await db.get(User, dev_id)
            if user is not None:
                return user
    user = (
        await db.execute(select(User).where(User.email == DEFAULT_USER_EMAIL))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=500, detail="identity seed not initialized")
    return user
```

```python
# backend/app/api/v1/users.py
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.identity import get_current_user
from app.db.models import User
from app.db.session import get_session
from app.schemas.user import UserOut

router = APIRouter(tags=["users"])   # no prefix -> mounted under /v1


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.get("/users", response_model=list[UserOut])
async def list_users(db: AsyncSession = Depends(get_session)) -> list[User]:
    return list((await db.execute(select(User).order_by(User.created_at))).scalars())
```

```python
# backend/app/api/v1/router.py  (modify)
from app.api.v1 import health, research, users  # add users

api_router = APIRouter(prefix="/v1")
api_router.include_router(health.router)
api_router.include_router(research.router)
api_router.include_router(users.router)   # -> /v1/me, /v1/users
```

- [ ] **Step 4: Run, verify pass** — `cd backend && ./.venv/bin/pytest tests/test_identity.py -v` → PASS (requires B3 merged).

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/identity.py backend/app/api/v1/users.py backend/app/api/v1/router.py backend/app/schemas/user.py backend/tests/test_identity.py
git commit -m "feat(api): identity seam (get_current_user) + /v1/me + /v1/users"
```

---

## Task B3: Idempotent seed + startup wiring

**Files:**
- Create: `backend/app/db/seed.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_seed.py`

**Interfaces:**
- Consumes: `User` (B1); `db_session` (B0); `SessionLocal` (startup).
- Produces: `seed_users(db: AsyncSession) -> None` (idempotent, keyed on email); `SEED_USERS`.

> **Sequencing:** B2's tests import `seed_users`. Implement B3 **before or together with** B2 so the suite is green.

- [ ] **Step 1: Failing test**

```python
# backend/tests/test_seed.py
from sqlalchemy import func, select
from app.db.models import User
from app.db.seed import seed_users


async def test_seed_is_idempotent(db_session):
    await seed_users(db_session); await db_session.commit()
    await seed_users(db_session); await db_session.commit()
    count = (await db_session.execute(select(func.count()).select_from(User))).scalar_one()
    assert count == 3
```

- [ ] **Step 2: Run, verify fail** — `cd backend && ./.venv/bin/pytest tests/test_seed.py -v` → FAIL (import).

- [ ] **Step 3: Implement**

```python
# backend/app/db/seed.py
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User

SEED_USERS = [
    {"email": "you@researcherx.dev", "name": "You", "avatar_color": "#2D3FE0"},
    {"email": "amelia@lab.io", "name": "Amelia Chen", "avatar_color": "#E0457F"},
    {"email": "marco@lab.io", "name": "Marco Rossi", "avatar_color": "#1FAE6B"},
]


async def seed_users(db: AsyncSession) -> None:
    """Create the demo users if absent. Idempotent (keyed on email)."""
    existing = {e for (e,) in await db.execute(select(User.email))}
    for spec in SEED_USERS:
        if spec["email"] not in existing:
            db.add(User(**spec))
    await db.flush()
```

- [ ] **Step 4: Run, verify pass** — same command → PASS.

- [ ] **Step 5: Wire startup** — in `backend/app/main.py` lifespan, **after** `_fail_orphaned_runs()` and before `yield`, seed using `SessionLocal`:

```python
# app/main.py — add import and call inside lifespan
from app.db.seed import seed_users
# ... inside lifespan, after await _fail_orphaned_runs():
async with SessionLocal() as db:
    await seed_users(db)
    await db.commit()
```

- [ ] **Step 6: Run full suite** — `cd backend && ./.venv/bin/pytest -q` → all green.

- [ ] **Step 7: Commit**

```bash
git add backend/app/db/seed.py backend/app/main.py backend/tests/test_seed.py
git commit -m "feat(db): seed demo users idempotently at startup"
```

---

## Task F1: Frontend foundation — shadcn/ui + Atlas/Slate theme tokens

**Files:**
- Modify: `frontend/package.json`, `frontend/tailwind.config.ts`, `frontend/src/app/globals.css`, `frontend/src/app/layout.tsx`
- Create: `frontend/components.json`, `frontend/src/lib/utils.ts`, `frontend/src/components/ui/*`, `frontend/src/components/theme-provider.tsx`

**Interfaces:**
- Produces: shadcn primitives (`button card input dropdown-menu avatar dialog tabs badge tooltip`); `cn()`; `ThemeProvider`; CSS variables for both themes; `--font-serif`, `--font-mono`; a `data-density` hook.

- [ ] **Step 1: Install deps**

```bash
cd frontend
npm i next-themes class-variance-authority clsx tailwind-merge lucide-react tailwindcss-animate
npx shadcn@latest init     # style: default; base color: slate; CSS variables: yes
npx shadcn@latest add button card input dropdown-menu avatar dialog tabs badge tooltip
```
Expected: `components.json`, `src/components/ui/*`, `src/lib/utils.ts` with `cn` exist.

- [ ] **Step 2: Theme tokens** — set the shadcn CSS-variable blocks in `globals.css` to the locked palette. Map these exact hexes onto the token names shadcn generated (convert to its channel format if it emitted HSL; keep the extras verbatim):

```
Atlas (:root)  --background #FCFCFD  --foreground #0B0F19  --card #FFFFFF
  --muted #F4F5F8  --muted-foreground #5B6172  --border/-input #E7E8EE
  --primary #2D3FE0  --primary-foreground #FFFFFF  --ring #2D3FE0
  --accent #EEF0F6  --accent-foreground #2D3FE0  --positive #0E9F6E  --radius .8rem
  --font-serif: ui-serif,"New York",Georgia,"Times New Roman",serif
  --font-mono:  ui-monospace,"SF Mono",Menlo,Consolas,monospace
Slate (.dark)  --background #0E1320  --foreground #E7EAF3  --card #151B2B
  --muted #1C2438  --muted-foreground #8B93A8  --border/-input #242E45
  --primary #5566EC  --primary-foreground #0E1320  --ring #5566EC
  --accent #1F2740  --accent-foreground #E7EAF3  --positive #2DD4BF
```

- [ ] **Step 3: Tailwind config** — `darkMode: "class"`; ensure shadcn color mappings present; `fontFamily.serif = ["var(--font-serif)"]`, `fontFamily.mono = ["var(--font-mono)"]`; radius from `--radius`; add `tailwindcss-animate` plugin.

- [ ] **Step 4: ThemeProvider** + wrap layout

```tsx
// frontend/src/components/theme-provider.tsx
"use client";
import { ThemeProvider as NextThemes } from "next-themes";
export function ThemeProvider({ children }: { children: React.ReactNode }) {
  return <NextThemes attribute="class" defaultTheme="light" enableSystem={false}>{children}</NextThemes>;
}
```
In `layout.tsx`: add `suppressHydrationWarning` to `<html>` and wrap `{children}` in `<ThemeProvider>`.

- [ ] **Step 5: Verify** — `npm run build` → compiles, no type errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/components.json frontend/tailwind.config.ts frontend/src/app/globals.css frontend/src/app/layout.tsx frontend/src/lib/utils.ts frontend/src/components/
git commit -m "feat(ui): shadcn + Atlas/Slate theme tokens"
```

---

## Task F2: API client + `User` type + identity context (dev act-as)

**Files:**
- Modify: `frontend/src/lib/types.ts`, `frontend/src/lib/api.ts`
- Create: `frontend/src/lib/identity.tsx`

**Interfaces:**
- Consumes: `GET /v1/me`, `GET /v1/users`; existing `API_BASE`.
- Produces: `type User`; `apiGet<T>(path)` injecting `X-Dev-User-Id`; `setDevUserId`; `IdentityProvider`, `useIdentity()` → `{ me, users, actAs }`.

- [ ] **Step 1: Type + client** (extend the existing `api.ts`; keep `API_BASE`, `createRun`, `getRun`, `eventsUrl`)

```ts
// frontend/src/lib/types.ts  (add)
export type User = { id: string; email: string; name: string; avatar_color: string };
```

```ts
// frontend/src/lib/api.ts  (add; do not remove existing exports)
let devUserId: string | null = null;
export const setDevUserId = (id: string | null) => { devUserId = id; };

export async function apiGet<T>(path: string): Promise<T> {
  const headers: Record<string, string> = {};
  if (devUserId) headers["X-Dev-User-Id"] = devUserId;
  const r = await fetch(`${API_BASE}/v1${path}`, { headers, cache: "no-store" });
  if (!r.ok) throw new Error(`GET ${path} -> ${r.status}`);
  return (await r.json()) as T;
}
```

- [ ] **Step 2: Identity context**

```tsx
// frontend/src/lib/identity.tsx
"use client";
import { createContext, useContext, useEffect, useState } from "react";
import { apiGet, setDevUserId } from "./api";
import type { User } from "./types";

type Ctx = { me: User | null; users: User[]; actAs: (id: string) => void };
const IdentityCtx = createContext<Ctx>({ me: null, users: [], actAs: () => {} });

export function IdentityProvider({ children }: { children: React.ReactNode }) {
  const [users, setUsers] = useState<User[]>([]);
  const [me, setMe] = useState<User | null>(null);
  const [actingId, setActingId] = useState<string | null>(null);

  useEffect(() => { setActingId(localStorage.getItem("devUserId")); }, []);
  useEffect(() => {
    setDevUserId(actingId);
    apiGet<User[]>("/users").then(setUsers).catch(() => {});
    apiGet<User>("/me").then(setMe).catch(() => {});
  }, [actingId]);

  const actAs = (id: string) => { localStorage.setItem("devUserId", id); setActingId(id); };
  return <IdentityCtx.Provider value={{ me, users, actAs }}>{children}</IdentityCtx.Provider>;
}
export const useIdentity = () => useContext(IdentityCtx);
```

- [ ] **Step 3: Build check** — `npm run build` → clean.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/
git commit -m "feat(ui): typed API client + identity context (dev act-as)"
```

---

## Task F3: App shell — top nav with theme/density toggles + identity menu

**Files:**
- Create: `frontend/src/components/app-shell.tsx`, `theme-toggle.tsx`, `density-toggle.tsx`, `user-menu.tsx`
- Modify: `frontend/src/app/layout.tsx`, `frontend/src/app/page.tsx`
- Create: `frontend/src/app/research/page.tsx`, `frontend/src/app/explorer/page.tsx`

**Interfaces:**
- Consumes: `useIdentity` (F2), shadcn primitives (F1), `next-themes` `useTheme`, `next/navigation` `usePathname`.
- Produces: `AppShell` wrapping pages with a top bar — wordmark `ResearcherX`, Research/Explorer nav (active from pathname, cobalt active state), theme toggle, density toggle (`document.documentElement.dataset.density`, persisted), user menu (avatar + "Acting as" picker over `users`, calls `actAs`).

- [ ] **Step 1: Build shell + toggles + user menu** matching the locked visual system. Density toggle sets `data-density="compact"|"comfortable"` on `<html>` and persists to localStorage. User menu uses shadcn `dropdown-menu` + `avatar`, lists `users`, shows `me.name`.

- [ ] **Step 2: Routing** — `layout.tsx` wraps children in `<IdentityProvider>` then `<AppShell>`. `page.tsx` redirects to `/research`. `research/page.tsx` + `explorer/page.tsx` render titled empty states ("Research projects" / "Discover papers across arXiv and Semantic Scholar") so nav works end to end.

- [ ] **Step 3: Verify** — `npm run build` clean. (Live click-through happens at the M0 checkpoint with the stack up.)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/ frontend/src/app/
git commit -m "feat(ui): app shell — nav, theme + density toggles, identity menu"
```

---

## Definition of done (M0)
- Backend: `cd backend && ./.venv/bin/pytest -q` green (60 existing + new). Frontend: `npm run build` clean.
- Live checkpoint (stack up): `/` → `/research`; shell renders Atlas, flips to Slate, density toggles; user menu lists You/Amelia/Marco and "act as" changes what `/v1/me` returns.

## Self-review (against spec §6.3, §7, §8)
- Identity seam (`get_current_user`, dev act-as, password_hash nullable/additive) → B1–B3 ✓
- Seeded users You/Amelia/Marco → B3 ✓
- Themed shell (Atlas/Slate, cobalt, serif-on-paper var, density, next-themes) → F1–F3 ✓
- Frozen `/v1` User contract + typed client → contract block + F2 ✓
- No pg/network in tests; `/v1` prefix; `SessionLocal`; conventions → constraints + B0 ✓
- pgvector/embeddings intentionally deferred to M2 ✓
