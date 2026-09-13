#!/usr/bin/env bash
# Lists tracked files that carry CR (Windows checkout without .gitattributes); exit 1 if any.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; cd "$REPO_ROOT"
bad="$(git ls-files -z | xargs -0 grep -lI $'\r' 2>/dev/null || true)"
if [[ -z "$bad" ]]; then echo "✔ checkout is LF-clean ($(git ls-files | wc -l) tracked files)"; exit 0; fi
echo "✖ CRLF in tracked files:"; echo "$bad" | sed 's/^/   /'
echo "   fix once, inside the repo:  git add --renormalize . && git rm -r -q --cached . && git reset -q --hard"
exit 1
