#!/usr/bin/env bash
set -eu

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

exec "$project_root/.venv/bin/python" -m uvicorn app:app --host 127.0.0.1 --port 8000
