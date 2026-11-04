# Docsmith — Design Specification

**Date:** 2026-10-30
**Status:** Draft for review
**Related:** `conformity-audit.md` (env/auth convergence), `vps-panel-project-setup.md` (deployment pattern), `deployment-notes/` (per-app deploy notes)

---

## 1. Overview

Docsmith is a document service for AI agents. The MVP exposes a **FastAPI REST API** secured by Docsmith-generated API keys. A Grok bot (user's own application calling the xAI API) calls these endpoints as its tool layer to search, read, create, and update the user's Google Docs. A minimal web dashboard handles sign-in, Google connection, and API key management.

### What Docsmith is NOT

- **No MCP server.** The original brief specified Remote MCP over Streamable HTTP; the decision is a plain REST API with Bearer API keys. Grok bot integrates by calling REST endpoints directly.
- **No document editor.** Google Docs remains the editor. The dashboard is configuration-only.
- **No billing, organisations, roles, or audit-log tables.** Minimal storage by explicit user decision.
- **No xAI backend integration.** Docsmith never calls the xAI API. There is no `XAI_API_KEY` in this project.

### Success criteria (MVP)

A user can: sign in to the dashboard, connect their Google account, generate an API key, configure their Grok bot with the base URL + key, and have the bot search/read/create/update professionally formatted Google Docs.

---

## 2. Key decisions (user-confirmed)

| Decision | Choice | Consequence |
|---|---|---|
| Frontend | React + Vite + TS + Tailwind | Matches budgethq/spliteasy/health-tracker house pattern; static nginx container |
| Sign-in | Email/password + `AUTHORIZED_USERS` allowlist, no public registration | Matches conformity auth pattern; tables multi-user-ready |
| Google auth | OAuth 2.0 web app, `drive.file` scope + Google Picker | Docs created land in the user's own Drive; Picker grants access to existing docs |
| OAuth publishing state | Stays in **Testing** | Refresh tokens expire after ~7 days; UI must handle reconnection gracefully (see §7.4) |
| Database scope | 3 tables only: `users`, `google_connections`, `api_keys` | No `document_operations` audit table; no usage logging |
| Agent interface | REST API + Bearer API key (no MCP) | Tool routes under `/api/v1/tools/*` |
| Full regeneration / delete safety | `confirm: true` request parameter required | Tool returns a preview + refusal if `confirm` is missing |

---

## 3. Architecture

```
Grok bot (user's app, calls xAI API itself)
        │  HTTPS, Authorization: Bearer ds_live_…
        ▼
┌──────────────────────────────────────────────┐
│ Docsmith backend (FastAPI, one container)    │
│  /api/auth/*      dashboard session (JWT)    │
│  /api/keys/*      API key mgmt (JWT)         │
│  /api/google/*    OAuth connect (JWT)        │
│  /api/v1/tools/*  agent tool API (API key)   │
│  /api/health      healthcheck                │
└──────────────┬───────────────────────────────┘
               │ google-api-python-client (refreshed creds)
               ▼
     Google Docs API + Drive API + Picker
```

- One FastAPI app serves dashboard routes and agent tool routes. Shared business logic; no duplicate implementations.
- Repository layout follows the house pattern (spliteasy-style backend layout is the reference):

```
docsmith/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py              # pydantic-settings, get_settings() @lru_cache
│   │   ├── database.py
│   │   ├── models.py              # SQLAlchemy: users, google_connections, api_keys
│   │   ├── schemas/               # Pydantic request/response models
│   │   ├── api/                   # routers: auth, keys, google, tools, health
│   │   ├── core/                  # security (JWT, hashing), api_key_auth, encryption
│   │   ├── services/              # google_oauth_service, api_key_service, document_service
│   │   └── document_engine/
│   │       ├── parser.py          # markdown-it-py → AST
│   │       ├── ast.py             # typed document AST
│   │       ├── renderer.py        # AST → provider request sequences
│   │       ├── themes.py          # named themes
│   │       └── providers/google_docs.py
│   ├── run_migrations.sh
│   ├── alembic/
│   └── Dockerfile
├── frontend/
│   ├── src/ (React + Vite + TS + Tailwind)
│   └── Dockerfile (nginx static)
├── docker-compose.yml             # prod
├── docker-compose.dev.yml         # dev (postgres:15, hot reload, Vite proxy)
├── Makefile
├── .env.example                   # single source of truth for env
├── CLAUDE.md                      # agent instructions (house style)
├── .github/workflows/{ci,deploy}.yml
└── docs/superpowers/              # gitignored specs/plans
```

---

## 4. Authentication model

Three fully independent mechanisms. **Never conflate them.**

### 4.1 Dashboard auth (user → Docsmith)

- Email/password. Passwords hashed with bcrypt/argon2.
- `AUTHORIZED_USERS` (comma-separated emails): if set, registration/login only succeeds for listed emails. **Empty = open** (list presence is the switch, per conformity audit). For this MVP the user will set their own email(s).
- JWT HS256; spliteasy pattern is the reference: short-lived access token in response body, httpOnly refresh cookie (`path=/api/auth`, `SameSite=lax`, `Secure` when `COOKIE_SECURE=true`), refresh rotation with token-family reuse detection, logout revokes all refresh rows for the user.
- Env: `JWT_SECRET_KEY`, `JWT_ACCESS_TOKEN_EXPIRE_MINUTES`, `JWT_REFRESH_TOKEN_EXPIRE_DAYS`, `COOKIE_SECURE`, `CORS_ORIGINS`.
- Registration endpoint creates a user but **fails with 403 if email not in `AUTHORIZED_USERS`** (when the allowlist is non-empty).

### 4.2 Google OAuth (Docsmith → Google)

- Google Cloud project "Docsmith"; enable **Google Docs API** and **Google Drive API**.
- OAuth client: Web application. Redirect URI `https://<domain>/api/google/callback` (dev: `http://localhost:8000/api/google/callback`).
- Scope (MVP, single scope):

  ```
  https://www.googleapis.com/auth/drive.file
  ```

  This grants access only to files created by the app or opened via Google Picker. Searching across the whole Drive is out of scope for MVP.
- **Google Picker** is embedded in the dashboard so users can grant access to existing docs (Picker issues an access token scoped per selected file; backend exchanges/uses it per Google Picker docs).
- Access tokens are **not persisted**; refreshed on demand from the encrypted refresh token.
- App stays in OAuth **Testing** publishing state: refresh tokens expire after 7 days of non-renewal. See §7.4.

### 4.3 API keys (Grok bot → Docsmith)

- Format: `ds_live_` + 32 bytes from `secrets.token_hex(16)` → e.g. `ds_live_8f3a7c9d…` (40 chars total incl. prefix). Generated with `secrets`, **never** `random`.
- Response of create endpoint returns the full plaintext key **exactly once**. DB stores only SHA-256 hash + 12-char prefix for display (`ds_live_8f3a…`).
- Table fields: name, key_hash (unique), key_prefix, created_at, last_used_at, revoked_at.
- Revocation is immediate: auth middleware checks `revoked_at IS NULL`.
- Bearer middleware on `/api/v1/tools/*`:
  1. Parse `Authorization: Bearer ds_live_…`.
  2. Hash, look up key, reject 401 if missing/revoked.
  3. Load key → user → active `google_connection`.
  4. If no active Google connection → 409 with a machine-readable error `google_not_connected`.
  5. Update `last_used_at` (fire-and-forget; no audit table).
- **Caller-supplied user identity is never accepted.** The API key is the only mapping to the user and their Google credentials.
- Tool endpoints enforce `confirm: true` for destructive/irreversible operations (full regeneration, delete); otherwise they return HTTP 422 with a preview and no mutation.

---

## 5. REST tool API (Grok bot interface)

Base: `https://<domain>/api/v1` (dev: `http://localhost:8000/api/v1`). All requests: `Authorization: Bearer ds_live_…`. All responses: JSON, include `document_url` where a document is involved, and machine-readable `error` codes on failure.

Auth errors: `401` (invalid/revoked key), `403` (key valid but Google token expired → `google_reconnect_required`), `404` (doc not found or not accessible under `drive.file`), `409` (revision conflict), `422` (validation / missing confirm).

### 5.1 Endpoints

| Method | Path | Purpose | Body (abridged) |
|---|---|---|---|
| POST | `/api/v1/tools/documents/search` | Search accessible docs by title/keywords | `{query, limit?}` → `{documents: [{id, name, url, modified_time}]}` |
| GET | `/api/v1/tools/documents/{id}` | Read content + structure + metadata | → `{id, title, url, revision_id, markdown, outline}` |
| POST | `/api/v1/tools/documents` | Create a new Google Doc from Markdown | `{title, markdown, theme?, return_format?}` |
| PATCH | `/api/v1/tools/documents/{id}` | Update existing doc | `{mode: "replace_section"\|"regenerate", …, confirm?}` |
| POST | `/api/v1/tools/documents/{id}/append` | Append Markdown at end of doc | `{markdown}` |
| POST | `/api/v1/tools/documents/{id}/delete` | Move doc to trash (POST avoids DELETE-with-body) | `{confirm: true}` |

### 5.2 Semantics

- **Read** converts the Google Docs JSON structure back to Markdown (headings, bold/italic, links, lists, tables, page breaks) so the agent can reason over it and feed it back for edits. Includes `revision_id` for later conflict-checked writes and an `outline` (heading → child index ranges) to support section targeting.
- **replace_section**: body `{section_heading, markdown, theme?, required_revision_id?, confirm?}`. The service re-reads the doc, locates the heading, computes its section range (heading + all content until the next heading of same-or-higher level or EOF), converts new Markdown to requests, and replaces the range. Everything outside the range is untouched.
- **regenerate**: body `{markdown, theme?, required_revision_id?, confirm?}`. Requires `confirm: true`; without it the endpoint returns `422` + a diff preview summary and makes no changes. With `confirm`, deletes all body content and re-renders from Markdown.
- **append**: converts Markdown and inserts at end-of-body index with a leading page-context check (no forced page break).
- **Concurrency**: every mutating endpoint accepts optional `required_revision_id`. When supplied, the write is submitted with the doc's current revision checked first; on mismatch the endpoint returns `409 conflict_detected` with the current revision so the agent can re-read and retry. Mutations also return the new `revision_id`.
- Every mutation response: `{document_id, document_url, revision_id, summary}` where `summary` is a human-readable change description (sections added/replaced, elements written).
- Rate of errors: transient Google API failures are retried with exponential backoff (max 3), except 4xx.

---

## 6. Document engine

Independent of Google specifics; `providers/google_docs.py` is the only Google-coupled module.

### 6.1 Pipeline

```
Markdown text → markdown-it-py → Document AST → theme applied → provider request list → documents.batchUpdate
```

- **parser.py**: markdown-it-py with tables enabled. No regex-based formatting.
- **ast.py**: typed node model — `Document`, `Heading(level)`, `Paragraph`, `Text(bold, italic, href)`, `List(ordered)`, `ListItem`, `Table(header, rows)`, `PageBreak`, `ImageRef` (reserved; out of MVP scope, see §10).
- **renderer.py**: walks the AST, emits an ordered list of Google Docs `batchUpdate` requests with **correct index tracking**. Indices are computed from a running cursor seeded by reading the current document (never hardcoded). Table requests are grouped per Google API constraints.
- **providers/google_docs.py**: executes request batches in chunks, refreshes credentials, maps API errors to service exceptions.

### 6.2 Supported Markdown (MVP)

- H1–H3 headings (mapped to named heading styles), title via doc title.
- Bold, italic, inline hyperlinks.
- Ordered and unordered lists.
- Tables with styled header rows (background colour, bold).
- Horizontal rules → page breaks.
- Paragraph spacing per theme.

### 6.3 Themes

Named theme objects applied at render time; default theme `professional`:

```python
Theme = {
    "font": "Arial",
    "body_size": 10.5,
    "title_size": 26,
    "heading_1_size": 18,
    "heading_2_size": 14,
    "heading_3_size": 12,
    "primary_colour": "#7138D8",
    "text_colour": "#252525",
    "page_margin": 54,          # points
    "table_header_bg": "#F3F0FC",
}
```

Themes are code-defined (no DB table). `create_document` and section/regenerate updates accept an optional `theme` name; default applies otherwise.

---

## 7. Google integration details

### 7.0 One-time Google Cloud setup (manual, by the user)

1. Create a Google Cloud project named "Docsmith" (console.cloud.google.com).
2. Enable the **Google Docs API** and **Google Drive API**.
3. Configure the OAuth consent screen: External, app name "Docsmith", user support email. Leave publishing status as **Testing**.
4. Add the user's own account(s) as **test users** (this permits use without completing Google OAuth verification).
5. Create OAuth credentials → Web application, with redirect URIs:
   - `https://docsmith.andrewallkin.cloud/api/google/callback` (production)
   - `http://localhost:8000/api/google/callback` (development)
6. Copy the client ID and secret into `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` in the panel-managed `.env`.

No service account is used (see §2 rationale: docs must live in the user's own Drive).

### 7.1 Connect flow

1. User clicks "Connect Google" in dashboard (JWT-authenticated).
2. Backend builds the auth URL (state = signed, short-lived JWT binding the user id) and 302s to Google.
3. Callback exchanges the code, fetches userinfo email, encrypts the refresh token, upserts `google_connections` (one per user in MVP), and redirects to the dashboard with a success/failure flag.
4. Disconnect: revokes the token via Google's revoke endpoint and nulls/deletes the row.

### 7.2 Token handling

- Refresh token encrypted with Fernet (`ENCRYPTION_KEY`, key held only in env — never in the DB; generation snippet documented in `.env.example`).
- Credential refresh happens lazily in `google_oauth_service` when a Google call needs credentials; new access tokens are used in-memory only.
- One Google connection per user in MVP; schema is designed to allow multiple later (unique `(user_id)` constraint for now).

### 7.3 Scopes & limits

- `drive.file` only. Search endpoint queries Drive (`files.list`) filtered to Docs, which under `drive.file` returns only app-created + picker-granted files. This limitation is documented in the dashboard UI so expectations are correct.
- Document Picker used in the dashboard for granting existing docs; the backend persists picker-granted file ids in memory only (access is granted by Drive automatically once the user picks them — no extra table).

### 7.4 Testing-mode token expiry (7-day)

- Google OAuth apps in Testing status issue refresh tokens that expire after 7 days.
- Detection: any Google call returning `invalid_grant` raises `GoogleReconnectRequired` → tool endpoints return `403 google_reconnect_required`; dashboard routes detect it on connection-status check.
- Dashboard shows a prominent "Reconnect Google" banner when the connection is expired/revoked. Reconnecting re-runs §7.1 and replaces the refresh token.

---

## 8. Database schema (minimal: 3 tables)

```sql
CREATE TABLE users (
    id UUID PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE google_connections (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    google_email TEXT NOT NULL,
    encrypted_refresh_token TEXT NOT NULL,
    scopes TEXT[] NOT NULL,
    connected_at TIMESTAMP NOT NULL DEFAULT NOW(),
    last_used_at TIMESTAMP,
    disconnected_at TIMESTAMP,          -- soft delete / revoke marker
    UNIQUE (user_id)                    -- one connection per user in MVP
);

CREATE TABLE api_keys (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,      -- SHA-256 of full key
    key_prefix VARCHAR(20) NOT NULL,    -- display: "ds_live_8f3a…"
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    last_used_at TIMESTAMP,
    revoked_at TIMESTAMP
);

CREATE INDEX idx_api_keys_user ON api_keys(user_id);
CREATE INDEX idx_google_connections_user ON google_connections(user_id);
```

Refresh sessions for JWT rotation live in a 4th table (`refresh_sessions`) required by the rotation/reuse-detection pattern — accepted minimal addition.

Alembic is the single source of truth; `backend/run_migrations.sh` (waits for DB, `alembic upgrade head`) is the panel's migration command.

---

## 9. Dashboard (frontend)

React + Vite + TS + Tailwind, static build served by nginx, relative `/api` base (same-origin; CORS is a dev-only concern via `CORS_ORIGINS`).

Pages:

1. **Login** — email + password; register link (allowlist-gated).
2. **Dashboard** —
   - Google Workspace card: connection status (connected / expired / not connected), connected email, scopes granted, Connect / Reconnect / Disconnect buttons, embedded **Google Picker** for granting existing docs.
   - API keys card: list (name, prefix, created, last used, status), Generate key modal (name field; plaintext shown once with copy button), Revoke button with confirm.
   - Grok bot integration card: base URL (`/api/v1`), curl examples for each tool endpoint, plain-language setup instructions for pointing the Grok bot at Docsmith.
3. **Settings** — password change; sign out everywhere (revokes refresh sessions).

No other pages. No usage logs (no audit table).

---

## 10. Out of scope (explicitly deferred)

- Images insertion / upload pipeline (`ImageRef` AST node reserved).
- Headers/footers/page numbers beyond default Docs behaviour (theme margins are in scope).
- Word/PDF providers, additional scopes (`documents`, full Drive beyond app-created/Picker-granted files), OAuth verification/production publishing.
- Images insertion / upload pipeline remains deferred; folders, sharing, comments, copy/export/restore operate on app-created and Picker-granted files only (drive.file).
- Usage/audit logging, billing, organisations, MCP server, xAI integration, webhooks.

---

## 11. Configuration & conformity

`.env.example` is the single source of truth (README links to it; no env tables in README). Sections use `# ---- Section ----` banners. Every `Settings` field appears in it.

```
# ---- Application ----
APP_NAME=Docsmith
ENVIRONMENT=development
BACKEND_PORT=8000
FRONTEND_PORT=3000

# ---- Database (panel-managed on the VPS) ----
POSTGRES_USER=docsmith
POSTGRES_PASSWORD=
POSTGRES_DB=docsmith
POSTGRES_HOST=localhost
POSTGRES_PORT=5432

# ---- Auth ----
JWT_SECRET_KEY=
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=15
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7
COOKIE_SECURE=false
CORS_ORIGINS=http://localhost:3000,http://localhost:5173
AUTHORIZED_USERS=                       # comma-separated emails; empty = open

# ---- Encryption (Fernet; python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())") ----
ENCRYPTION_KEY=

# ---- Google OAuth ----
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=http://localhost:8000/api/google/callback
```

Conformity checklist applied: pydantic-settings `backend/app/config.py`, `get_settings()` with `@lru_cache`, `extra="ignore"`; canonical env names; `.env` chmod 600, never committed; `./data:/app/data` bind mount present for future use; `docs/superpowers/` gitignored; uv for Python dependency management; feature-branch workflow, never direct commits to main.

---

## 12. Compose, Makefile, CI

**docker-compose.yml (prod)** — house pattern:
- `backend`: `image: andrewallkin/docsmith:${DOCKER_IMAGE_BACKEND:-backend-main-latest}`, `env_file: .env`, `127.0.0.1:${BACKEND_PORT:-8000}:8000`, healthcheck `http://127.0.0.1:8000/api/health`, external network `postgres-net`, `restart: always`.
- `frontend`: `image: andrewallkin/docsmith:${DOCKER_IMAGE_FRONTEND:-frontend-main-latest}`, `127.0.0.1:${FRONTEND_PORT:-3000}:80`, `depends_on: backend: service_healthy`, `restart: always`.

**docker-compose.dev.yml** — postgres:15 service with `pg_isready` healthcheck, backend hot-reload (`uv run uvicorn … --reload`), Vite frontend with `/api` proxy to backend.

**Makefile** — `dev-up`, `dev-build`, `dev-down`, `dev-logs`, `migrate`, `migrate-create MSG='…'`, `migrate-rollback`, `lint`, `test`, `prod-up`, `prod-down`.

**CI (deploy.yml, push to main + workflow_dispatch):**
1. `test` job: `uv sync --frozen --extra dev` + `pytest` with inline dummy env (incl. dummy `ENCRYPTION_KEY`, `JWT_SECRET_KEY`).
2. `build` job: build + push `andrewallkin/docsmith:backend-<branch>-<sha12>` / `frontend-…` plus `backend-<branch>-latest` / `frontend-<branch>-latest`.
3. CI never SSHes, writes `.env`, starts containers, or runs migrations — the VPS control panel does deploys.

**Panel registration (manual, by user):** slug `docsmith`, Docker Hub image mode, branch `main`, compose `docker-compose.yml`, routes `/api` → backend port, `/` → frontend port, `/api/v1` covered by the `/api` prefix (no extra route needed), DB created in central Postgres, migration command `bash /app/run_migrations.sh`, health check `http://127.0.0.1:<BACKEND_PORT>/api/health`. A deployment note will be added at `/Users/andrewallkin/personal/repos/deployment-notes/docsmith.md`.

---

## 13. Testing

Unit tests (mocked Google calls):

- API key: generation format, hash storage (never plaintext), create-once display, auth success, 401 invalid, 401 revoked, `last_used_at` updates, user isolation (key of user A cannot reach user B's Google connection — enforced by schema lookup, tested explicitly).
- JWT auth: registration (allowlist pass/block), login, refresh rotation, reuse detection → logout-all, logout.
- Document engine: Markdown → AST snapshots (headings, inline styles, lists, tables, links, page breaks); AST → Google request sequences with correct index tracking (fixture-based, no hardcoded indices); theme application; replace_section range computation (mid-document, last section, missing heading); regenerate preview vs confirmed.
- Google services: token refresh, `invalid_grant` → `GoogleReconnectRequired` → 403 mapping, disconnect revocation.
- Tool endpoints: full request/response contract per endpoint incl. confirm-missing → 422, revision conflict → 409, `google_not_connected` → 409.

Integration (manual, staged): connect Google with a test account, create/read/update real docs, 7-day expiry simulation (revoke token server-side), end-to-end Grok bot script.

---

## 14. Definition of done

| Test | Expected result |
|---|---|
| Register + login | Only allowlisted emails; JWT + refresh cookie works; rotation & reuse detection behave |
| Connect Google | Consent flow completes; encrypted refresh token stored; email shown in dashboard |
| Reconnect flow | Expired/revoked token → banner + 403 `google_reconnect_required` from tools; reconnect fixes |
| Generate API key | Plaintext shown once; hash stored; prefix listed |
| Invalid/revoked key | 401 on all tool routes |
| Search documents | Returns accessible (app-created/picker) Docs only |
| Read document | Returns Markdown + metadata + revision_id |
| Create document | Native Google Doc in user's Drive, theme applied, correct formatting |
| Append / replace section | Only intended range changes; rest preserved |
| Regenerate without confirm | 422 + preview, no change |
| Revision conflict | 409 when `required_revision_id` stale |
| Delete without confirm | 422; with confirm → trashed |
| Cross-user isolation | User A's key never touches user B's connection |

---

## 15. References

- Google Docs API: https://developers.google.com/workspace/docs/api
- Google Drive API: https://developers.google.com/workspace/drive/api/guides/about-sdk
- Docs OAuth scopes: https://developers.google.com/workspace/docs/api/auth
- Google Picker: https://developers.google.com/picker
- xAI Responses API (consumed by Grok bot, not Docsmith): https://docs.x.ai
