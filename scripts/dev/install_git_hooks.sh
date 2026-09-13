#!/usr/bin/env bash
# Installs a pre-commit hook that refuses to commit env files or obvious secrets.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HOOK="$REPO_ROOT/.git/hooks/pre-commit"
cat >"$HOOK" <<'EOF'
#!/usr/bin/env bash
# mr-load secret guard (installed by scripts/dev/install_git_hooks.sh)
set -u
bad=0
for f in $(git diff --cached --name-only --diff-filter=ACM); do
  case "$f" in
    .env|.env.*|*/.env|*/.env.*) [[ "$f" == *.env.mrload.example ]] || { echo "✖ refusing to commit env file: $f"; bad=1; } ;;
  esac
  if git show ":$f" 2>/dev/null | grep -qE 'pat-(eu|na)[0-9]-[0-9a-f-]{20,}|"private_key"\s*:\s*"-----BEGIN|AIza[0-9A-Za-z_-]{35}|ya29\.[0-9A-Za-z_-]{30,}'; then
    echo "✖ refusing to commit $f: looks like it contains a HubSpot/Google secret"; bad=1
  fi
done
[[ $bad -eq 0 ]] || { echo "   (bypass only if you are sure: git commit --no-verify)"; exit 1; }
EOF
chmod +x "$HOOK" && echo "✔ pre-commit secret guard installed at $HOOK"
