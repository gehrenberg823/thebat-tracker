#!/bin/bash
# Daily refresh of the The Bat manual-trade tracker: re-query ClickHouse and
# rebuild index.html. Local only — nothing is pushed anywhere.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/$(date +%Y-%m-%d).log"

PYTHON="${PYTHON:-/Library/Frameworks/Python.framework/Versions/3.13/bin/python3}"
[ -x "$PYTHON" ] || PYTHON="python3"

{
  echo "=== run started $(date -Iseconds) ==="
  "$PYTHON" refresh.py
  echo "=== run finished $(date -Iseconds) ==="
} >> "$LOG_FILE" 2>&1
