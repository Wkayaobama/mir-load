#!/usr/bin/env bash
# Side pane: live ledger/artefact status + the latest step log. Ctrl-C to stop.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; cd "$REPO_ROOT"
VENV="${MRLOAD_VENV:-.venv}"; [[ -f "$VENV/bin/activate" ]] && source "$VENV/bin/activate"
while true; do
  clear
  scripts/run_pass1.sh status 2>/dev/null || true
  latest=$(ls -t .mrload/logs/*.log 2>/dev/null | head -1)
  if [[ -n "$latest" ]]; then echo; echo "── $latest ──"; tail -n 25 "$latest"; fi
  sleep 5
done
