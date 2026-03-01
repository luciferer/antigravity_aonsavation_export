#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MODE="${1:-v2}"

if [[ "$MODE" == "legacy" ]]; then
  TARGET="$SCRIPT_DIR/server.py"
  PORT="${WATCHDOG_PORT:-18888}"
  EXTRA_ARGS=()
else
  TARGET="$SCRIPT_DIR/server_v2.py"
  PORT="${WATCHDOG_PORT:-18888}"
  LOG_ROOT="${WATCHDOG_LOG_ROOT:-$HOME/Desktop/conversation_threads}"
  EXTRA_ARGS=(
    --port "$PORT"
    --log-root "$LOG_ROOT"
    --max-threads "${WATCHDOG_MAX_THREADS:-20}"
    --thinking-threshold-seconds "${WATCHDOG_THINKING_THRESHOLD_SECONDS:-90}"
  )
  if [[ "${WATCHDOG_ENABLE_LEGACY_MIRROR:-1}" == "1" ]]; then
    EXTRA_ARGS+=(
      --enable-legacy-mirror
      --legacy-md "${WATCHDOG_LEGACY_MD:-$HOME/Desktop/conversation_log.md}"
      --legacy-db "${WATCHDOG_LEGACY_DB:-$HOME/Desktop/conversation_log.db}"
    )
  fi
fi

LOG_FILE="${WATCHDOG_START_LOG:-/tmp/conversation_watchdog.log}"
nohup python3 "$TARGET" "${EXTRA_ARGS[@]}" > "$LOG_FILE" 2>&1 &
PID=$!

sleep 0.5
if ps -p "$PID" > /dev/null 2>&1; then
  echo "Watchdog server started in background (PID $PID)."
  echo "Mode: $MODE"
  echo "Port: $PORT"
  echo "Logs: $LOG_FILE"
else
  echo "Failed to start watchdog server. Check logs: $LOG_FILE" >&2
  exit 1
fi
