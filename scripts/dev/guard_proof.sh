#!/usr/bin/env bash
# Proves the git safety net: .env is ignored, nothing env-like is tracked, and the pre-commit
# guard refuses a force-staged .env. Leaves the index exactly as it found it.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; cd "$REPO_ROOT"
echo "── ignore rule ──"; git check-ignore -v .env .env.mrload || echo "✖ .env is NOT ignored — check .gitignore"
echo "── tracked env files: $(git ls-files .env .env.mrload | wc -l) (0 expected) ──"
[[ -f .env ]] || { echo "no .env to test with (scripts/dev/env_clinic.sh --fix creates one)"; exit 1; }
[[ -x .git/hooks/pre-commit ]] || { echo "✖ pre-commit guard missing — run scripts/dev/install_git_hooks.sh"; exit 1; }
git add -f .env
git commit -q -m guard-test >/dev/null 2>&1; rc=$?
git reset -q -- .env
if [[ $rc -ne 0 ]]; then echo "✔ commit refused by the guard (rc=$rc) — .env unstaged again"; exit 0; fi
echo "✖ UNEXPECTED: the commit went through. Undo now:  git reset --soft HEAD~1 && git reset -q -- .env"; exit 1
