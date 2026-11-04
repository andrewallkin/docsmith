# CLAUDE.md

House-style agent instructions for the Docsmith repo.

## Stack

- Backend: FastAPI, SQLAlchemy (sync), Alembic, pydantic-settings (`app/config.py`,
  `get_settings()` with `@lru_cache`, `extra="ignore"`), uv for dependency management
  (`cd backend && uv sync`). Migrations are the single source of truth for schema.
- Frontend: React + Vite + TS + Tailwind, relative `/api` base (same-origin in prod).
- Deploy: docker-compose, images built/pushed by CI (`andrewallkin/docsmith:backend-*`
  / `frontend-*`); the VPS control panel runs deploys and migrations (`bash /app/run_migrations.sh`).

## Commands

- `cd backend && uv run pytest` — backend tests
- `cd backend && uv run ruff check .` — backend lint
- `cd frontend && npm run lint && npm run type-check` — frontend lint/typecheck
- `make migrate-create MSG="..."` — new alembic revision (dev compose must be up)

## Rules

- Never read the contents of the .env file if it exists.
- Never commit directly to `main`; use feature branches.
- Never commit `.env` or secrets. `ENCRYPTION_KEY`, `JWT_SECRET_KEY`, Google client
  secret live only in the panel-managed `.env`.
- API keys are never stored in plaintext: DB holds SHA-256 hash + 12-char prefix only.
- Google refresh tokens are always encrypted (Fernet) at rest; access tokens are never persisted.
- Tool endpoints never accept caller-supplied user identity — the API key is the
  only mapping to the user and their Google credentials.
- Destructive tool operations require `confirm: true`; without it return 422 + preview.
- Don't add features listed in the design spec's "Out of scope" section.
