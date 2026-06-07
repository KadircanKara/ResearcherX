.PHONY: up down logs build migrate revision fmt lint test

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

revision:
	docker compose exec backend alembic revision --autogenerate -m "$(m)"

fmt:
	cd backend && ruff format .
	cd frontend && npm run format

lint:
	cd backend && ruff check .
	cd frontend && npm run lint
