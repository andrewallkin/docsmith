FROM python:3.12-slim AS base
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock /app/backend/
COPY app /app/backend/app
COPY alembic /app/backend/alembic
COPY alembic.ini /app/backend/alembic.ini
COPY run_migrations.sh /app/run_migrations.sh
RUN chmod +x /app/run_migrations.sh

RUN cd /app/backend && uv sync --frozen --no-dev
ENV PATH="/app/backend/.venv/bin:$PATH"

EXPOSE 8000
CMD ["uv", "run", "--project", "/app/backend", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
