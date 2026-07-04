#!/bin/bash
set -Eeuo pipefail

PROJECT_DIR="/Users/kelvinhsueh/Documents/taiwan_trading/taiwan_short_term_trading"
SCRIPT_DIR="$PROJECT_DIR/scripts"
AGENT_DIR="$HOME/Library/LaunchAgents"
MAIN_LABEL="com.kelvin.taiwan-paper-pipeline"
RETRY_LABEL="com.kelvin.taiwan-paper-pipeline-retry"
MAIN_PLIST="$SCRIPT_DIR/${MAIN_LABEL}.plist"
RETRY_PLIST="$SCRIPT_DIR/${RETRY_LABEL}.plist"

install_agent() {
  local label="$1"
  local source_plist="$2"
  local dest_plist="$AGENT_DIR/${label}.plist"

  if [ ! -f "$source_plist" ]; then
    echo "Missing plist: $source_plist" >&2
    exit 2
  fi

  echo "Installing $label"
  launchctl unload "$dest_plist" >/dev/null 2>&1 || true
  cp "$source_plist" "$dest_plist"
  launchctl load "$dest_plist"
  launchctl list | grep "$label" || {
    echo "Warning: $label did not appear in launchctl list output immediately." >&2
  }
}

if [ ! -d "$PROJECT_DIR" ]; then
  echo "Project directory does not exist: $PROJECT_DIR" >&2
  exit 2
fi

mkdir -p "$AGENT_DIR"
mkdir -p "$PROJECT_DIR/logs"

install_agent "$MAIN_LABEL" "$MAIN_PLIST"

if [ "${1:-}" = "--with-retry" ]; then
  install_agent "$RETRY_LABEL" "$RETRY_PLIST"
else
  echo "Retry agent not installed. To add the 04:30 retry, run:"
  echo "  bash scripts/install_launchd_pipeline.sh --with-retry"
fi

echo "Done. Installed LaunchAgent plist(s) in $AGENT_DIR"

