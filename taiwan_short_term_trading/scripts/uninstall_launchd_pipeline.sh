#!/bin/bash
set -Eeuo pipefail

AGENT_DIR="$HOME/Library/LaunchAgents"
MAIN_LABEL="com.kelvin.taiwan-paper-pipeline"
RETRY_LABEL="com.kelvin.taiwan-paper-pipeline-retry"

uninstall_agent() {
  local label="$1"
  local plist="$AGENT_DIR/${label}.plist"

  echo "Uninstalling $label"
  launchctl unload "$plist" >/dev/null 2>&1 || true
  rm -f "$plist"
}

uninstall_agent "$MAIN_LABEL"
uninstall_agent "$RETRY_LABEL"

echo "Done. Removed Taiwan paper pipeline LaunchAgent plist(s)."

