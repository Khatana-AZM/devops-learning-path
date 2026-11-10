FROM python:3.12-slim

# Don't write .pyc, don't buffer stdout (so logs show up in `docker logs`).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /code

# Requirements first: this layer is cached until requirements.txt changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini .
COPY migrations ./migrations
COPY app ./app

# Run as a non-root user.
RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /code
USER appuser

# Documentation only -- it publishes nothing. The port actually bound is
# $PORT at runtime, defaulting to 8000.
EXPOSE 8000

# Hosted platforms (Render, Northflank, Fly, Cloud Run) inject $PORT and
# expect the process to bind it. A hardcoded port means the health check
# never passes and the platform reports a timeout rather than the real cause.
#
# `exec` matters: without it, sh stays PID 1 and uvicorn is its child, so
# SIGTERM on shutdown hits sh and the server is killed rather than draining.
# With exec, uvicorn replaces sh and receives the signal itself.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
