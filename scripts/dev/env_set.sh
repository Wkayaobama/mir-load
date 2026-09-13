#!/usr/bin/env bash
# =============================================================================
# mr-load — set ONE key in .env without ever echoing the value.
#   printf '%s' "$value" | scripts/dev/env_set.sh KEY     # value from stdin (notebook / pipes)
#   scripts/dev/env_set.sh KEY                            # interactive: silent prompt
# Replaces an existing KEY= line (uncommenting a "#KEY=" template line if that is
# all there is), otherwise appends. Always writes LF endings and mode 600.
# Prints only the key name and the value length.
# =============================================================================
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; cd "$REPO_ROOT"
KEY="${1:-}"
[[ "$KEY" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || { echo "usage: env_set.sh KEY  (value on stdin)" >&2; exit 2; }
if [[ -t 0 ]]; then read -rs -p "$KEY (input hidden): " VAL; echo; else IFS= read -r VAL || true; fi
VAL="${VAL%$'\r'}"
[[ -n "$VAL" ]] || { echo "✖ empty value for $KEY — nothing written" >&2; exit 1; }
[[ -f .env ]] || { cp .env.mrload.example .env; echo "✔ created .env from .env.mrload.example"; }
K="$KEY" V="$VAL" python3 - <<'PY'
import os, re
k, v, p = os.environ["K"], os.environ["V"], ".env"
s = open(p, newline="").read().replace("\r\n", "\n")
live = re.compile(r"^" + re.escape(k) + r"=.*$", re.M)
tmpl = re.compile(r"^#\s*" + re.escape(k) + r"=.*$", re.M)
line = f"{k}={v}"
if live.search(s):   s, how = live.sub(lambda m: line, s, count=1), "replaced"
elif tmpl.search(s): s, how = tmpl.sub(lambda m: line, s, count=1), "uncommented + set"
else:                s, how = (s.rstrip("\n") + "\n" if s else "") + line + "\n", "appended"
open(p, "w", newline="").write(s); os.chmod(p, 0o600)
print(f"✔ {k} {how} in .env ({len(v)} chars, masked)")
PY
