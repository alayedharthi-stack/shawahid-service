#!/bin/sh
set -e

# Run migrations only when DATABASE_URL is configured.
# This allows the health check to pass before the database is provisioned,
# and ensures migrations run automatically once DATABASE_URL is set.
if [ -n "$DATABASE_URL" ]; then
  echo "[shawahid] Running database migrations..."
  alembic upgrade head
  echo "[shawahid] Migrations complete."
else
  echo "[shawahid] WARNING: DATABASE_URL not set — skipping migrations. Set DATABASE_URL and redeploy."
fi

echo "[shawahid] Starting server on port ${PORT:-8010}..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8010}" --workers 1
