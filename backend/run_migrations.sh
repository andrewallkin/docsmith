#!/usr/bin/env bash
# Wait for the database, then run all migrations.
# Executed by the VPS control panel: docker compose run --rm --no-deps backend bash /app/run_migrations.sh
set -euo pipefail

cd /app/backend

echo "Waiting for database..."
for i in $(seq 1 30); do
  if uv run alembic current >/dev/null 2>&1; then
    echo "Database is up."
    break
  fi
  if [ "$i" = "30" ]; then
    echo "Database did not become ready in time." >&2
    exit 1
  fi
  sleep 2
done

echo "Current revision:"
uv run alembic current
echo "Upgrading to head..."
uv run alembic upgrade head
echo "Done. New revision:"
uv run alembic current
