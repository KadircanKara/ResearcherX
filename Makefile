.PHONY: up down logs build migrate revision fmt lint test prod-up prod-down prod-logs claude-proxy demo-up demo-down demo-logs demo-backup

up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f

build:
	docker compose build

# Migrations run automatically at backend startup; this is the manual
# escape hatch (e.g. against a stopped app).
migrate:
	docker compose exec backend alembic upgrade head

test:
	docker compose exec backend pytest -q

# Prod stack (docker-compose.prod.yml) under its own project name so it can
# coexist with the dev stack. Requires POSTGRES_PASSWORD, LLM_API_KEY,
# OWNER_API_KEY and EMBEDDING_API_KEY in the environment (from SSM on the
# box; export locally).
prod-up:
	docker compose -p researcherx-prod -f docker-compose.prod.yml up -d --build

prod-down:
	docker compose -p researcherx-prod -f docker-compose.prod.yml down

prod-logs:
	docker compose -p researcherx-prod -f docker-compose.prod.yml logs -f

# Public demo: prod compose + docker-compose.demo.yml (see deploy/RUNBOOK.md).
DEMO = docker compose -p researcherx-demo -f docker-compose.prod.yml -f docker-compose.demo.yml

demo-up:
	$(DEMO) up -d --build

demo-down:
	$(DEMO) down

demo-logs:
	$(DEMO) logs -f

# Nightly from cron (deploy/RUNBOOK.md); keeps 7 days.
demo-backup:
	mkdir -p backups && $(DEMO) exec -T db pg_dump -U researcherx researcherx | gzip > backups/researcherx-$$(date -u +%F).sql.gz && find backups -name 'researcherx-*.sql.gz' -mtime +7 -delete

revision:
	docker compose exec backend alembic revision --autogenerate -m "$(m)"

fmt:
	cd backend && ruff format .
	cd frontend && npm run format

lint:
	cd backend && ruff check .
	cd frontend && npm run lint

# Dev-only: OpenAI-compatible endpoint backed by the Claude Code CLI, so the
# pipeline's LLM calls run on your `claude` login instead of an API key. Runs
# on the HOST (the binary and its credentials are not in the container).
# Point .env at it and recreate the backend — see tools/claude-proxy/README.md.
claude-proxy:
	python3 tools/claude-proxy/server.py
