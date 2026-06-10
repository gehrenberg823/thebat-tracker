#!/bin/bash
# Install (or reinstall) the daily launchd agent for the The Bat tracker.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PLIST_NAME="com.gregehrenberg.thebat-tracker"
SRC="$HERE/$PLIST_NAME.plist"
DEST="$HOME/Library/LaunchAgents/$PLIST_NAME.plist"

chmod +x "$HERE/run_daily.sh"
cp "$SRC" "$DEST"
chmod 644 "$DEST"

# Reload cleanly so re-running this picks up plist changes.
launchctl bootout "gui/$(id -u)/$PLIST_NAME" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$DEST"

echo "Installed ${PLIST_NAME} — runs daily at 9:00 AM local time."
echo "Test now:  launchctl kickstart gui/$(id -u)/${PLIST_NAME}"
