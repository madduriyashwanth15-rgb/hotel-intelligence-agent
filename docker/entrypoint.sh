#!/bin/sh
# Demo start-up (idempotent): migrate -> seed once -> ingest knowledge base -> serve the API.
# Every step is safe to repeat on restart; nothing here downloads anything.
set -eu

echo "[demo] 1/4 applying database migrations"
alembic upgrade head

echo "[demo] 2/4 loading synthetic data (only if the database is empty)"
hotel-seed --if-empty

echo "[demo] 3/4 ingesting the knowledge base (idempotent)"
hotel-kb ingest

echo "[demo] 4/4 starting the API on :8000 (published on 127.0.0.1 only; NO authentication)"
exec uvicorn hotel_agent.api.app:create_app --factory --host 0.0.0.0 --port 8000
