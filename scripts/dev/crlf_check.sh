#!/usr/bin/env bash
# Lists tracked files that carry CR (Windows checkout without .gitattributes); exit 1 if any.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; cd "$REPO_ROOT"
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  bad="$(git ls-files -z | xargs -0 grep -lI $'\r' 2>/dev/null || true)"; n="$(git ls-files | wc -l) tracked files"
else   # unzipped download: no index → scan the tree (a GitHub zip is LF; a Windows editor may not be)
  bad="$(grep -rlI $'\r' --exclude-dir=.mrload --exclude-dir=.venv --exclude-dir=target --exclude-dir=dbt_packages --exclude-dir=__pycache__ . 2>/dev/null || true)"; n="scanned tree (no git index)"
fi
if [[ -z "$bad" ]]; then echo "✔ checkout is LF-clean ($n)"; exit 0; fi
echo "✖ CRLF in tracked files:"; echo "$bad" | sed 's/^/   /'
echo "   fix once, inside the repo:  git add --renormalize . && git rm -r -q --cached . && git reset -q --hard"
exit 1
