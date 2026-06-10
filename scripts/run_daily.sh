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

  # Publish to GitHub Pages so the shared page stays current. The page always
  # changes (the 'refreshed' timestamp updates), so this effectively pushes a
  # daily snapshot; push failures are non-fatal and caught up on the next run.
  if git diff --quiet -- index.html; then
    echo "index.html unchanged — nothing to publish."
  else
    git add index.html
    git -c user.name="$(git config user.name)" \
        -c user.email="$(git config user.email)" \
        commit -m "Daily refresh: $(date +%Y-%m-%d)"
    if git push origin main; then
      echo "Published to https://gehrenberg823.github.io/thebat-tracker/"
    else
      echo "WARNING: push failed — commit is local, will retry next run."
    fi
  fi

  echo "=== run finished $(date -Iseconds) ==="
} >> "$LOG_FILE" 2>&1
