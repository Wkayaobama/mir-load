#!/usr/bin/env bash
# =============================================================================
# mr-load — .env clinic (safe to run anywhere; prints MASKED values only).
#   scripts/dev/env_clinic.sh            # inspect
#   scripts/dev/env_clinic.sh --fix      # also: create .env from template, strip CRLF, chmod 600
# Checks: which env files exist and their precedence, required keys, placeholder
# values left from the template, CRLF endings (Windows editors), file mode,
# and that git can never commit them (.gitignore, tracked/staged, pre-commit guard).
# =============================================================================
set -uo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; cd "$REPO_ROOT"
FIX=0; [[ "${1:-}" == "--fix" ]] && FIX=1
ok()   { printf '\033[1;32m✔ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m⚠ %s\033[0m\n' "$*"; }
bad()  { printf '\033[1;31m✖ %s\033[0m\n' "$*"; ISSUES=$((ISSUES+1)); }
ISSUES=0
REQUIRED=(MRLOAD_BQ_PROJECT HUBSPOT_SANDBOX_TOKEN)
OPTIONAL=(MRLOAD_BQ_DATASET MRLOAD_BQ_RAW_DATASET MRLOAD_BQ_LOCATION GOOGLE_APPLICATION_CREDENTIALS HUBSPOT_SANDBOX_PORTAL_ID MRLOAD_DEAL_PIPELINE MRLOAD_DEAL_STAGE MRLOAD_LEDGER_PATH MRLOAD_CACHE_DIR)
PLACEHOLDERS='your-gcp-project-id|xxxxxxxx|/absolute/path|^00000000$|<.*>'

mask() { local v="$1"; local n=${#v}; (( n == 0 )) && { echo "(empty)"; return; }; (( n <= 4 )) && { echo "****"; return; }; echo "${v:0:4}…(${n} chars)"; }

echo "── env files (precedence: process env > .env > .env.mrload > ../.env.mrload) ──"
FOUND=0
for f in ".env" ".env.mrload" "../.env.mrload"; do
  if [[ -f "$f" ]]; then FOUND=1; printf '  %-16s present  mode %s  %s lines\n' "$f" "$(stat -c %a "$f" 2>/dev/null || stat -f %Lp "$f")" "$(wc -l <"$f")"
  else printf '  %-16s -\n' "$f"; fi
done
if [[ $FOUND -eq 0 ]]; then
  if [[ $FIX -eq 1 ]]; then cp .env.mrload.example .env && chmod 600 .env && ok "created .env from .env.mrload.example (fill it, then rerun)"
  else bad "no env file — run with --fix to create .env from the template"; fi
fi

FSTYPE="$(df -T . 2>/dev/null | awk 'NR==2{print $2}')"
case "$FSTYPE" in 9p|fuse.grpcfuse|drvfs|cifs|virtiofs|fakeowner|fuse.osxfs) WINMOUNT=1;; *) WINMOUNT=0;; esac
for f in .env .env.mrload ../.env.mrload; do
  [[ -f "$f" ]] || continue
  if grep -q $'\r' "$f"; then
    if [[ $FIX -eq 1 ]]; then sed -i 's/\r$//' "$f" && ok "$f: CRLF stripped"; else bad "$f has CRLF line endings — every value would carry a trailing \\r (gcloud: 'project not found'); rerun with --fix"; fi
  fi
  if [[ "$(stat -c %a "$f" 2>/dev/null)" != "600" ]]; then
    if [[ $FIX -eq 1 ]]; then chmod 600 "$f" 2>/dev/null; fi
    mode="$(stat -c %a "$f" 2>/dev/null)"
    if [[ "$mode" == "600" ]]; then ok "$f: mode set to 600"
    elif [[ $FIX -eq 1 || $WINMOUNT -eq 1 ]]; then warn "$f mode is $mode and cannot be changed here (${FSTYPE:-bind mount} — Windows/macOS checkout mounted into Docker; expected on that route, the file stays git-ignored)"
    else warn "$f mode is not 600 (chmod 600 $f)"; fi
  fi
done

declare -A EFF
for f in ../.env.mrload .env.mrload .env; do      # later files override earlier = documented precedence
  [[ -f "$f" ]] || continue
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%#*}"; line="${line#"${line%%[![:space:]]*}"}"; line="${line%"${line##*[![:space:]]}"}"
    [[ "$line" == *=* ]] || continue
    k="${line%%=*}"; k="${k#export }"; v="${line#*=}"
    [[ "$k" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] && EFF["$k"]="$v"
  done <"$f"
done
for k in "${REQUIRED[@]}" "${OPTIONAL[@]}"; do [[ -n "${!k+x}" ]] && EFF["$k"]="${!k}"; done

echo "── effective values (masked) ──"
for k in "${REQUIRED[@]}"; do
  v="${EFF[$k]:-}"
  if [[ -z "$v" ]]; then bad "$k is missing"
  elif [[ "$v" =~ $PLACEHOLDERS ]]; then bad "$k still holds the template placeholder ($(mask "$v"))"
  else ok "$k = $(mask "$v")"; fi
done
for k in "${OPTIONAL[@]}"; do
  v="${EFF[$k]:-}"; [[ -z "$v" ]] && continue
  if [[ "$v" =~ $PLACEHOLDERS ]]; then bad "$k holds a template placeholder ($(mask "$v"))"; else printf '  %-32s %s\n' "$k" "$(mask "$v")"; fi
done
if [[ -n "${EFF[GOOGLE_APPLICATION_CREDENTIALS]:-}" ]]; then
  [[ -f "${EFF[GOOGLE_APPLICATION_CREDENTIALS]}" ]] && ok "GOOGLE_APPLICATION_CREDENTIALS file exists (service-account route)" \
    || bad "GOOGLE_APPLICATION_CREDENTIALS points to a missing file — comment it out for the gauth route"
else
  ok "GOOGLE_APPLICATION_CREDENTIALS unset → user ADC route (scripts/gauth.sh)"
fi
t="${EFF[HUBSPOT_SANDBOX_TOKEN]:-}"
[[ -n "$t" && ! "$t" =~ ^pat-(eu|na)[0-9]- ]] && warn "HUBSPOT_SANDBOX_TOKEN does not look like a private-app token (pat-eu1-… / pat-na1-…)"
[[ "${EFF[HUBSPOT_SANDBOX_PORTAL_ID]:-}" == "9201667" ]] && bad "HUBSPOT_SANDBOX_PORTAL_ID is 9201667 = ICALPS PRODUCTION — sandbox first"
for k in "${!EFF[@]}"; do [[ "${EFF[$k]}" == *$'\r'* ]] && bad "$k value carries \\r"; done

echo "── git safety ──"
git check-ignore -q .env && ok ".env is ignored by git" || bad ".env is NOT ignored (check .gitignore)"
git check-ignore -q .env.mrload && ok ".env.mrload is ignored by git" || bad ".env.mrload is NOT ignored"
tracked="$(git ls-files | grep -E '(^|/)\.env(\.|$)' | grep -v '\.env\.mrload\.example$' || true)"
[[ -z "$tracked" ]] && ok "no env file is tracked (only .env.mrload.example)" || bad "TRACKED env file(s): $tracked"
staged="$(git diff --cached --name-only | grep -E '(^|/)\.env(\.|$)' | grep -v example || true)"
[[ -z "$staged" ]] && ok "no env file staged" || bad "STAGED env file(s): $staged — git restore --staged <file>"
{ [[ -x .git/hooks/pre-commit ]] && grep -q mr-load .git/hooks/pre-commit; } && ok "pre-commit secret guard installed" || warn "pre-commit guard not installed → scripts/dev/install_git_hooks.sh"
echo
(( ISSUES == 0 )) && ok "env clinic: no blocking issue" || { bad "env clinic: $ISSUES blocking issue(s)"; exit 1; }
