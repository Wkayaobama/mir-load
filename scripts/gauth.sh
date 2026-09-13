#!/usr/bin/env bash
# =============================================================================
# mr-load — Google authentication for Drive + BigQuery on a laptop / WSL /
# Docker / Cloud Shell, as YOU (no service account), then a live Drive check.
#
#   scripts/gauth.sh              # interactive: prints URLs, you paste codes back
#
# Why this exact sequence (verified against the gcloud reference):
#   * `gcloud auth application-default login --scopes=…drive…` is REJECTED:
#     gcloud's own OAuth client may not request Drive scopes on the ADC path
#     ("create an OAuth Client ID and provide it using --client-id-file").
#   * `gcloud auth login --enable-gdrive-access --update-adc` is allowed and
#     writes those user credentials to the ADC file the pipeline reads.
#   * User credentials calling a non-Cloud API (Drive) need a QUOTA PROJECT,
#     and the Drive API must be enabled on it, or the first call fails with
#     PERMISSION_DENIED "requires a quota project".
# =============================================================================
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$REPO_ROOT"
# Files never override variables already present in the process environment,
# so callers (the e2e rehearsal, CI, a one-off `VAR=x scripts/run_pass1.sh`)
# always win over an operator's .env.
_load_env() {
  local f line key val
  for f in "$REPO_ROOT/.env" "$REPO_ROOT/.env.mrload" "$REPO_ROOT/../.env.mrload"; do
    [[ -f "$f" ]] || continue
    while IFS= read -r line || [[ -n "$line" ]]; do
      line="${line%%#*}"; line="${line#"${line%%[![:space:]]*}"}"; line="${line%"${line##*[![:space:]]}"}"
      [[ "$line" == *=* ]] || continue
      key="${line%%=*}"; key="${key#export }"; key="${key%"${key##*[![:space:]]}"}"
      [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
      [[ -n "${!key+x}" ]] && continue
      val="${line#*=}"; val="${val#"${val%%[![:space:]]*}"}"
      [[ "$val" == \"*\" && "$val" == *\" ]] && val="${val:1:${#val}-2}"
      [[ "$val" == \'*\' && "$val" == *\' ]] && val="${val:1:${#val}-2}"
      export "$key=$val"
    done < "$f"
  done
}
_load_env
say() { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }
ok()  { printf '\033[1;32m✔ %s\033[0m\n' "$*"; }
die() { printf '\033[1;31m✖ %s\033[0m\n' "$*" >&2; exit 1; }
command -v gcloud >/dev/null || die "gcloud missing — run scripts/bootstrap_ubuntu.sh first"
[[ -n "${MRLOAD_BQ_PROJECT:-}" ]] || die "MRLOAD_BQ_PROJECT unset in .env (the BigQuery project; it is also the quota project for Drive calls)"
ROOT_ID="$(python3 -c "import yaml;print(yaml.safe_load(open('context/cards/library.yaml'))['scope']['roots'][0]['drive_id'])")"
BROWSER_FLAG="--no-launch-browser"; [[ "${GAUTH_BROWSER:-0}" == "1" ]] && BROWSER_FLAG=""

say "1/4 project"
gcloud config set project "$MRLOAD_BQ_PROJECT" >/dev/null
ok "core/project = $MRLOAD_BQ_PROJECT"

say "2/4 user login with Drive access, written to ADC (consent screen: BigQuery + Drive)"
gcloud auth login --enable-gdrive-access --update-adc $BROWSER_FLAG
ok "ADC written: $(gcloud info --format='value(config.paths.global_config_dir)')/application_default_credentials.json"

say "3/4 quota project + APIs"
gcloud auth application-default set-quota-project "$MRLOAD_BQ_PROJECT" >/dev/null
gcloud services enable drive.googleapis.com bigquery.googleapis.com --project="$MRLOAD_BQ_PROJECT" >/dev/null
ok "quota project set, Drive + BigQuery APIs enabled on $MRLOAD_BQ_PROJECT"

say "4/4 live check: can the ADC identity see the scope root?"
TOKEN="$(gcloud auth application-default print-access-token)"
RESP="$(curl -sS -H "Authorization: Bearer $TOKEN" -H "x-goog-user-project: $MRLOAD_BQ_PROJECT" \
  "https://www.googleapis.com/drive/v3/files/$ROOT_ID?supportsAllDrives=true&fields=id,name,driveId")"
if echo "$RESP" | grep -q '"name"'; then
  ok "Drive OK → $(echo "$RESP" | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d["name"], "(shared drive)" if d.get("driveId") else "")')"
else
  echo "$RESP" >&2
  die "Drive check failed. 'requires a quota project' → rerun this script; 'insufficientPermissions' → the login above did not include Drive (answer the consent screen fully); 404 → the account cannot see the scope root folder"
fi
BQ="$(bq --project_id="$MRLOAD_BQ_PROJECT" query --use_legacy_sql=false --format=csv 'select 1 as ok' 2>/dev/null | tail -1)"
[[ "$BQ" == "1" ]] && ok "BigQuery OK on $MRLOAD_BQ_PROJECT" || die "BigQuery query failed — is billing/BigQuery enabled on $MRLOAD_BQ_PROJECT?"
echo; ok "auth complete — next: scripts/run_pass1.sh preflight"
