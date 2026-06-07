# Multi-stage: `dev` (compose default for local work) and `prod` (default
# target — what CI builds and the box runs). Base image is multi-arch, so the
# same Dockerfile builds linux/arm64 for the Graviton box via buildx in CI.

FROM python:3.12-slim AS base
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir -U pip


# ── dev ─────────────────────────────────────────────────────────────────────
# docker-compose.yml builds this target, mounts ./backend over /app and runs
# uvicorn --reload. Dev extras are baked in so `make test` works after any
# container recreate.
FROM base AS dev
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[dev]"
COPY . .
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]


# ── prod (default) ──────────────────────────────────────────────────────────
FROM base AS prod

# Install the dependency set non-editably, then drop the (empty) package
# itself: the app runs from /app source because migrate.py resolves
# alembic/ relative to the tree and uvicorn imports app/ from the workdir.
# Only pyproject.toml is present at this layer, so it caches until deps change.
COPY pyproject.toml ./
RUN pip install --no-cache-dir . && pip uninstall -y researcherx-backend

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

# Non-root; /app stays root-owned read-only — the app writes nothing to disk.
RUN useradd --create-home --uid 1000 appuser
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/v1/health', timeout=2).status == 200 else 1)"]

# --workers 1 is LOAD-BEARING: the event bus, task registry, and rate-limiter
# storage are in-process. Scaling beyond one worker requires the Redis
# bus/queue upgrade documented in the README.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
