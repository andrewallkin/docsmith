# Docsmith

Document service for AI agents. A Grok bot calls Docsmith's REST API (`/api/v1/tools/*`)
with a Bearer API key to create and update professionally formatted Google Docs.

- Design spec: `docs/specs/docsmith-design.md`
- Configuration: `.env.example` (single source of truth; copy to `.env`)

## Layout

```
backend/    FastAPI (uv, SQLAlchemy, Alembic, google-api-python-client)
frontend/   React + Vite + TS + Tailwind dashboard (nginx static)
```

## Local development

```bash
cp .env.example .env        # then fill in secrets
make dev-up                 # postgres + backend (hot reload) + frontend (Vite)
make migrate                # alembic upgrade head
```

Frontend on http://localhost:3000, backend on http://localhost:8000.

## Commands

- `make dev-build|dev-up|dev-down|dev-logs`
- `make migrate`, `make migrate-create MSG="..."`, `make migrate-rollback`
- `make lint`, `make test`
- `make prod-up`, `make prod-down` (uses Docker Hub images via `DOCKER_IMAGE_*`)

## Conventions

- Feature-branch workflow; never commit directly to `main`.
- `docs/superpowers/` is gitignored — working specs/plans live there.
- Tool API auth: Bearer API key (`ds_live_...`), hashed with SHA-256, plaintext shown once.
