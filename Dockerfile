# Multi-stage build: uv install in builder → slim runtime.
# Architecture §9: single container, 256MB RAM target, non-root runtime.

# ---------- Stage 1: builder ----------
FROM python:3.12-slim AS builder

# uv: install the binary to /usr/local/bin.
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /usr/local/bin/

WORKDIR /app

# Install deps first (cache layer). --frozen requires uv.lock; if absent,
# fall back to resolution. We commit uv.lock for reproducible Coolify builds.
COPY pyproject.toml uv.lock* README.md ./

# Sync into a venv at /app/.venv. Dev extras are NOT installed in the image.
RUN uv sync --frozen --no-dev --no-install-project || \
    uv sync --no-dev --no-install-project

# Now copy the source and install the project itself.
COPY src/ ./src/
COPY alembic/ ./alembic/
COPY alembic.ini ./
COPY entrypoint.sh ./

RUN uv sync --frozen --no-dev || uv sync --no-dev

# ---------- Stage 2: runtime ----------
FROM python:3.12-slim AS runtime

# Runtime deps for psycopg-binary and healthcheck curl.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Non-root user (NFR-4 / #034 security).
RUN groupadd --system --gid 1001 app && \
    useradd --system --uid 1001 --gid app --home-dir /app --shell /usr/sbin/nologin app

WORKDIR /app

# Copy the venv from the builder.
COPY --from=builder --chown=app:app /app/.venv /app/.venv
# Copy application code + alembic + entrypoint.
COPY --from=builder --chown=app:app /app/src ./src
COPY --from=builder --chown=app:app /app/alembic ./alembic
COPY --from=builder --chown=app:app /app/alembic.ini ./alembic.ini
COPY --chown=app:app entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh

# Put the venv on PATH so `uvicorn`, `alembic` resolve directly.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Europe/Bucharest

USER app

EXPOSE 8000

# Coolify watches this; container restarts on failure.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# Entrypoint runs migrations only for the webhook app. The same image also
# runs the scheduler-only command in Coolify.
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "leetcode_coach.main:app", "--host", "0.0.0.0", "--port", "8000"]
