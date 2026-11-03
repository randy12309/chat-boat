#!/usr/bin/env bash
# Starts the MCP server, backend API and Angular frontend. Ctrl+C stops all three.
set -u
export NG_CLI_ANALYTICS=false   # skip the interactive Angular analytics prompt
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

pids=()
cleanup() {
  echo; echo "Stopping..."
  for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null; done
  wait 2>/dev/null
}
trap cleanup EXIT INT TERM

cd "$ROOT/backend"
python -m mcp_server.server & pids+=($!)
python -m uvicorn app.main:app --port 8000 --reload & pids+=($!)

cd "$ROOT/frontend-ng"
[ -d node_modules ] || npm install
echo "Open http://localhost:4200  (login: randy / password)"
npm start & pids+=($!)

wait
