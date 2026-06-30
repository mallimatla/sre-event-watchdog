#!/usr/bin/env bash
# SRE Event Watchdog — one-command demo launcher (macOS / Linux)
#
#   Starts: (1) mock alert receiver on :8001, (2) watchdog API + dashboard on :8000.
#   First run creates a venv and installs requirements.
#
# Usage:  ./scripts/run.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# --- venv ---
if [ ! -d ".venv" ]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv
fi
PY="$ROOT/.venv/bin/python"

echo "Installing dependencies..."
"$PY" -m pip install --quiet --upgrade pip
"$PY" -m pip install --quiet -r requirements.txt

# --- mock receiver (background) ---
echo "Starting mock alert receiver on http://localhost:8001 ..."
"$PY" -m uvicorn mock_receiver.main:app --port 8001 &
RECEIVER_PID=$!
trap 'echo "Shutting down mock receiver..."; kill $RECEIVER_PID 2>/dev/null || true' EXIT
sleep 2

# --- watchdog API + dashboard (foreground) ---
cat <<EOF

==================================================================
  Dashboard : http://localhost:8000/
  API docs  : http://localhost:8000/docs
  Health    : http://localhost:8000/api/health
  Mock recv : http://localhost:8001/received
==================================================================

EOF

"$PY" -m app
