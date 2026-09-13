#!/usr/bin/env bash
# =============================================================================
# mr-load — non-interactive verification of Google + HubSpot credentials.
# Prints masked identities only. Run after scripts/gauth.sh and after .env is set.
# =============================================================================
set -uo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; cd "$REPO_ROOT"
ok()   { printf '\033[1;32m✔ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m⚠ %s\033[0m\n' "$*"; }
bad()  { printf '\033[1;31m✖ %s\033[0m\n' "$*"; ISSUES=$((ISSUES+1)); }
ISSUES=0
for f in ../.env.mrload .env.mrload .env; do [[ -f "$f" ]] && { set -a; . "$f"; set +a; }; done
ROOT_ID="$(python3 -c "import yaml;print(yaml.safe_load(open('context/cards/library.yaml'))['scope']['roots'][0]['drive_id'])")"

echo "── Google ──"
if ! command -v gcloud >/dev/null; then bad "gcloud not installed (scripts/bootstrap_ubuntu.sh)"; else
  acct="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null | head -1)"
  [[ -n "$acct" ]] && ok "gcloud active account: ${acct%%@*}@…" || bad "no active gcloud account (scripts/gauth.sh)"
  proj="$(gcloud config get-value project 2>/dev/null)"
  [[ -n "$proj" ]] && ok "gcloud core/project: $proj" || warn "gcloud core/project unset"
  [[ "${MRLOAD_BQ_PROJECT:-}" == "$proj" ]] || warn "MRLOAD_BQ_PROJECT (${MRLOAD_BQ_PROJECT:-unset}) differs from gcloud project (${proj:-unset})"
  ADC="${CLOUDSDK_CONFIG:-$HOME/.config/gcloud}/application_default_credentials.json"
  if [[ -n "${GOOGLE_APPLICATION_CREDENTIALS:-}" ]]; then
    [[ -f "$GOOGLE_APPLICATION_CREDENTIALS" ]] && ok "service-account key present (SA route)" || bad "GOOGLE_APPLICATION_CREDENTIALS file missing"
  elif [[ -f "$ADC" ]]; then
    python3 - "$ADC" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1])); qp = d.get("quota_project_id")
print(("\033[1;32m✔\033[0m" if qp else "\033[1;31m✖\033[0m"), f"ADC type={d.get('type')} quota_project={qp or 'MISSING → gcloud auth application-default set-quota-project <project>'}")
EOF
  else bad "no ADC file at $ADC and no service-account key → run scripts/gauth.sh"; fi
  if tok="$(gcloud auth application-default print-access-token 2>/dev/null)"; then
    resp="$(curl -sS -m 20 -H "Authorization: Bearer $tok" -H "x-goog-user-project: ${MRLOAD_BQ_PROJECT:-}" \
      "https://www.googleapis.com/drive/v3/files/$ROOT_ID?supportsAllDrives=true&fields=id,name")"
    if echo "$resp" | grep -q '"name"'; then ok "Drive: scope root readable → $(echo "$resp" | python3 -c 'import json,sys;print(json.load(sys.stdin)["name"])')"
    else bad "Drive check failed: $(echo "$resp" | python3 -c 'import json,sys;e=json.load(sys.stdin).get("error",{});print(e.get("status") or e.get("code"), (e.get("message") or "")[:140])' 2>/dev/null || echo "$resp" | head -c 200)"; fi
    if command -v bq >/dev/null && [[ -n "${MRLOAD_BQ_PROJECT:-}" ]]; then
      r="$(bq --project_id="$MRLOAD_BQ_PROJECT" query --use_legacy_sql=false --format=csv 'select 1 as ok' 2>&1 | tail -1)"
      [[ "$r" == "1" ]] && ok "BigQuery: query OK on $MRLOAD_BQ_PROJECT" || bad "BigQuery query failed: ${r:0:160}"
    fi
  else bad "cannot mint an ADC access token → scripts/gauth.sh"; fi
fi

echo "── HubSpot ──"
if [[ -z "${HUBSPOT_SANDBOX_TOKEN:-}" ]]; then bad "HUBSPOT_SANDBOX_TOKEN unset"; else
  info="$(curl -sS -m 20 -H "Authorization: Bearer $HUBSPOT_SANDBOX_TOKEN" "${MRLOAD_HUBSPOT_API_BASE:-https://api.hubapi.com}/account-info/v3/details")"
  pid="$(echo "$info" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("portalId",""))' 2>/dev/null)"
  if [[ -n "$pid" ]]; then
    ok "token valid → portal $pid ($(echo "$info" | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d.get("accountType",""), d.get("timeZone",""))'))"
    [[ "$pid" == "9201667" ]] && bad "portal 9201667 is ICALPS PRODUCTION (WISeKey SA) — use the sandbox token first"
  else bad "HubSpot rejected the token: $(echo "$info" | head -c 160)"; fi
fi
echo
(( ISSUES == 0 )) && ok "auth check: all green" || { bad "auth check: $ISSUES issue(s)"; exit 1; }
