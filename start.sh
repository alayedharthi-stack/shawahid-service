#!/bin/sh
set -e

echo "[shawahid] Running database migrations..."
alembic upgrade head

echo "[shawahid] Starting server on port ${PORT:-8010}..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8010}" --workers 1
