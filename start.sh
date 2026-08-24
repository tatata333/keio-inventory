#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
# デモDB（SQLite）を構築してから起動する
export PYTHONPATH="src"
export DATABASE_URL="sqlite:///keio_demo.db"
python -m jobs.setup_sqlite
echo "[+] starting uvicorn..."
exec python -m uvicorn keio_inventory.api.main_db:app --host 0.0.0.0 --port "${PORT:-8001}"
