#!/bin/bash
# AJSAA run wrapper — used by cron or manual invocation
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

if [ -f ".env" ]; then
    set -a
    source .env
    set +a
fi

exec .venv/bin/python run.py "$@"
