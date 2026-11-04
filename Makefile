.PHONY: dev-up dev-build dev-down dev-logs migrate migrate-create migrate-rollback lint test prod-up prod-down

dev-build:
	docker compose -f docker-compose.dev.yml build

dev-up:
	docker compose -f docker-compose.dev.yml up -d

dev-down:
	docker compose -f docker-compose.dev.yml down

dev-logs:
	docker compose -f docker-compose.dev.yml logs -f

migrate:
	docker compose -f docker-compose.dev.yml exec backend bash /app/run_migrations.sh

migrate-create:
	docker compose -f docker-compose.dev.yml exec backend uv run alembic revision -m "$(MSG)"

migrate-rollback:
	docker compose -f docker-compose.dev.yml exec backend uv run alembic downgrade -1

lint:
	cd backend && uv run ruff check .
	cd frontend && npm run lint

test:
	cd backend && uv run pytest

prod-up:
	docker compose up -d

prod-down:
	docker compose down
