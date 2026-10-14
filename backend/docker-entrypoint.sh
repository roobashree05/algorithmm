#!/bin/sh
set -eu

alembic upgrade head

if [ "${SEED_DEMO_DATA:-true}" = "true" ]; then
  python -m app.db.seed
fi

exec uvicorn app.main:app \
  --host "${APP_HOST:-0.0.0.0}" \
  --port "${APP_PORT:-8000}" \
  --access-log \
  --no-access-log
