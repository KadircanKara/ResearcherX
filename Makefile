.PHONY: up down logs build migrate revision fmt lint test

up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f

build:
	docker compose build

migrate:
	docker compose exec backend alembic upgrade head

revision:
	docker compose exec backend alembic revision --autogenerate -m "$(m)"

fmt:
	cd backend && ruff format .
	cd frontend && npm run format

lint:
	cd backend && ruff check .
	cd frontend && npm run lint
