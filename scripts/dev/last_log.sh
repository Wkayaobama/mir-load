#!/usr/bin/env bash
# Shows the newest pipeline logs and the tail of the latest one.  last_log.sh [lines]
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; cd "$REPO_ROOT"
LOGS="${MRLOAD_STATE_DIR:-.mrload}/logs"; N="${1:-40}"
ls -t "$LOGS" 2>/dev/null | head -5 || true
f="$(ls -t "$LOGS"/* 2>/dev/null | head -1)"
[[ -n "$f" ]] || { echo "no logs yet in $LOGS"; exit 0; }
echo "== $f (last $N lines)"; tail -n "$N" "$f"
