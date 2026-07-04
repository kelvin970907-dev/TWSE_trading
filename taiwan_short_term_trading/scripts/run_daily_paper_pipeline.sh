#!/bin/bash
set -Eeuo pipefail

PROJECT_DIR="/Users/kelvinhsueh/Documents/taiwan_trading/taiwan_short_term_trading"
DB_PATH="data/taiwan_trading.duckdb"
LOG_TIMEZONE="America/Chicago"
RUN_DATE="$(TZ="$LOG_TIMEZONE" date +%F)"

if [ ! -d "$PROJECT_DIR" ]; then
  echo "Project directory does not exist: $PROJECT_DIR" >&2
  exit 2
fi

LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/daily_pipeline_${RUN_DATE}.log"
ERROR_LOG="$LOG_DIR/daily_pipeline_errors.log"

mkdir -p "$LOG_DIR"
touch "$LOG_FILE" "$ERROR_LOG"

exec >> "$LOG_FILE" 2>&1

on_error() {
  local exit_code=$?
  local line_no="${1:-unknown}"
  {
    echo "[$(TZ="$LOG_TIMEZONE" date '+%Y-%m-%d %H:%M:%S %Z')] FAILURE exit_code=${exit_code} line=${line_no} log=${LOG_FILE}"
  } >> "$ERROR_LOG"
  echo "Daily paper pipeline failed with exit code ${exit_code}; see ${LOG_FILE}"
  exit "$exit_code"
}
trap 'on_error $LINENO' ERR

die() {
  local exit_code="$1"
  shift
  local message="$*"
  echo "$message" >&2
  {
    echo "[$(TZ="$LOG_TIMEZONE" date '+%Y-%m-%d %H:%M:%S %Z')] FAILURE exit_code=${exit_code} message=${message} log=${LOG_FILE}"
  } >> "$ERROR_LOG"
  exit "$exit_code"
}

echo "============================================================"
echo "Taiwan closed-limit-up paper pipeline"
echo "Started: $(TZ="$LOG_TIMEZONE" date '+%Y-%m-%d %H:%M:%S %Z')"
echo "Project: $PROJECT_DIR"
echo "Log: $LOG_FILE"
echo "Paper trading only: no real orders are submitted by this pipeline."
echo "============================================================"

cd "$PROJECT_DIR"

if [ ! -f "$DB_PATH" ]; then
  die 3 "DuckDB database is missing: $PROJECT_DIR/$DB_PATH"
fi

if [ -f ".venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
  echo "Activated virtual environment: .venv"
elif [ -f "venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "venv/bin/activate"
  echo "Activated virtual environment: venv"
else
  echo "No virtual environment found; using python from PATH."
fi

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
export PYTHONUNBUFFERED=1
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$PROJECT_DIR${PYTHONPATH:+:$PYTHONPATH}"
export LANG="en_US.UTF-8"
export LC_ALL="en_US.UTF-8"
export TZ="$LOG_TIMEZONE"
export TAIWAN_PAPER_TRADING_ONLY=1

if command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
else
  die 4 "No python or python3 executable found on PATH."
fi

echo "Python: $("$PYTHON_BIN" --version)"
echo "Running daily paper pipeline..."

"$PYTHON_BIN" -m src.live.run_daily_closed_limit_up_pipeline \
  --db "$DB_PATH" \
  --capital-twd 1000000 \
  --profile all \
  --market BOTH \
  --output-dir reports/live_signals

LATEST_REPORT="$(find reports/live_signals -maxdepth 1 -name 'daily_pipeline_report_*.md' -type f -print 2>/dev/null | sort | tail -n 1 || true)"

echo "Completed: $(TZ="$LOG_TIMEZONE" date '+%Y-%m-%d %H:%M:%S %Z')"
if [ -n "$LATEST_REPORT" ]; then
  echo "Latest pipeline report: $PROJECT_DIR/$LATEST_REPORT"
else
  echo "Pipeline completed, but no daily_pipeline_report_*.md file was found."
fi
