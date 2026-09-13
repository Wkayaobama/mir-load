#!/usr/bin/env bash
# =============================================================================
# mr-load — scripted execution of the library pipeline, steps 0 → 7.
#
#   scripts/run_pass1.sh <step> [--yes]
#
# Steps (run in this order; each is idempotent and safe to re-run):
#   preflight       0. verify tools, env, SA sharing, HubSpot token, BigQuery access
#   walk            0. Drive API DFS → .mrload/library_hierarchy.csv (+ silver preview)
#   bq-init         B. create datasets + empty ledger tables (dbt sources resolve)
#   bq-load         1a. hierarchy CSV → mrload_raw.library_hierarchy        [gate BQ_LOAD]
#   hs-props        P.  HubSpot property DEFINITIONS from the card (StackSync targets) [gate PROPERTY_CREATE]
#   dbt             2a+3a. dbt deps / run / test (+ docs generate → catalog) — the cardinality gate
#   hs-props-verify P'. definitions vs built silver → .mrload/review/stacksync_mapping.csv
#   review          operator queues + deal_decisions.csv template (offline)
#   companies-dry   1b. search-only resolution report
#   companies-live  2b. create missing companies                             [gate COMPANY_CREATE]
#   attach-dry      3b. row count per company, nothing fired
#   attach-upload   4b. phase 1: Drive → HubSpot Files                       [gate FILES_UPLOAD]
#   attach-notes    5b. phase 2: note + association → company                [gate FILE_NOTES_POST]
#   ledger-export   6.  ledger → CSV → mrload_raw.* + dbt build              [gate BQ_LOAD]
#   deals-dry       7.  pass 2 dry run from the edited decisions file
#   deals-live      7.  pass 2 create deals + associations                   [gate DEAL_CREATE]
#   status          ledger + artefact summary
#   all             preflight → … → ledger-export with a checkpoint before every live gate
#
# Gates are set INLINE per live step (never exported globally). Every live step
# first re-runs its dry counterpart and asks for confirmation unless --yes.
# Logs: .mrload/logs/<step>-<timestamp>.log
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
PY="${PYTHON:-python3}"
RUNNER="$PY -m pipeline.library_files.runner"
STATE="${MRLOAD_STATE_DIR:-.mrload}"
HIER="$STATE/library_hierarchy.csv"
SILVER="$STATE/silver_preview.csv"
REVIEW="$STATE/review"
LEDGER="${MRLOAD_LEDGER_PATH:-$STATE/ledger.sqlite}"
HS_BASE="${MRLOAD_HUBSPOT_API_BASE:-https://api.hubapi.com}"
LOGS="$STATE/logs"
mkdir -p "$STATE" "$LOGS"

# ── env layering (process env > .env > .env.mrload) ──────────────────────────
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
RAW_DS="${MRLOAD_BQ_RAW_DATASET:-mrload_raw}"
SILVER_DS="${MRLOAD_BQ_DATASET:-mrload}"
LOCATION="${MRLOAD_BQ_LOCATION:-EU}"
DBT_TARGET="${MRLOAD_DBT_TARGET:-dev}"
YES=0; [[ "${2:-}" == "--yes" || "${YES_ALL:-0}" == "1" ]] && YES=1

# ── helpers ──────────────────────────────────────────────────────────────────
ts() { date -u +%Y%m%dT%H%M%SZ; }
say() { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }
ok()  { printf '\033[1;32m✔ %s\033[0m\n' "$*"; }
warn(){ printf '\033[1;33m⚠ %s\033[0m\n' "$*"; }
die() { printf '\033[1;31m✖ %s\033[0m\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "missing tool: $1 ($2)"; }
confirm() {  # confirm "<question>"  — honours --yes
  [[ $YES -eq 1 ]] && return 0
  read -r -p "$1 [type YES to proceed] " ans; [[ "$ans" == "YES" ]] || die "aborted by operator"
}
logrun() {  # logrun <step> <cmd...>  — tee stdout+stderr to a log file
  local step="$1"; shift
  local log="$LOGS/$step-$(ts).log"
  echo "# $(date -u) :: $*" >"$log"
  # stdout stays clean (callers capture JSON from it); stderr is shown and logged
  "$@" 2> >(tee -a "$log" >&2) | tee -a "$log"
  return "${PIPESTATUS[0]}"
}
count_json() {  # count_json <file> <python-expr over d>
  $PY -c "import json,sys; d=json.load(open('$1')); print($2)"
}
ledger_sql() { $PY - "$LEDGER" "$1" <<'EOF'
import sqlite3, sys
con = sqlite3.connect(sys.argv[1]); print(con.execute(sys.argv[2]).fetchall())
EOF
}

# ── steps ────────────────────────────────────────────────────────────────────
step_preflight() {
  say "0/preflight — tools"
  need "$PY" "python3"; need bq "gcloud SDK: https://cloud.google.com/sdk"; need curl "curl"
  $PY -c "import googleapiclient, google.auth, yaml, requests" 2>/dev/null \
    || { warn "python deps missing → pip install -r requirements.txt"; $PY -m pip install -q -r requirements.txt; }
  command -v dbt >/dev/null 2>&1 || warn "dbt not on PATH (pip install dbt-bigquery) — needed for steps dbt/ledger-export"

  say "0/preflight — environment"
  [[ -n "${MRLOAD_BQ_PROJECT:-}" ]] || die "MRLOAD_BQ_PROJECT unset (see .env.mrload.example)"
  if [[ -n "${GOOGLE_APPLICATION_CREDENTIALS:-}" ]]; then
    [[ -f "$GOOGLE_APPLICATION_CREDENTIALS" ]] || die "SA key not found: $GOOGLE_APPLICATION_CREDENTIALS"
    SA_EMAIL=$($PY -c "import json;print(json.load(open('$GOOGLE_APPLICATION_CREDENTIALS'))['client_email'])")
    ok "service account: $SA_EMAIL"
    echo "   → OPERATOR: the scope root folder must be shared with $SA_EMAIL (Viewer)."
  else
    warn "GOOGLE_APPLICATION_CREDENTIALS unset → Application Default Credentials will be used"
  fi
  [[ -n "${HUBSPOT_SANDBOX_TOKEN:-}" ]] || warn "HUBSPOT_SANDBOX_TOKEN unset → HubSpot steps stay DRY"

  say "0/preflight — Drive sharing check (depth-1 walk of the scope root)"
  local probe="$STATE/preflight_probe.csv"
  if logrun preflight-walk $RUNNER walk --max-depth 1 --hierarchy-out "$probe" >/dev/null; then
    local n; n=$($PY -c "import csv;print(sum(1 for _ in csv.DictReader(open('$probe'))))")
    [[ "$n" -gt 0 ]] && ok "scope root visible: $n depth-1 nodes (segments)" \
      || die "walk returned 0 nodes — the SILENT-FAILURE step: share the root with the SA email, or check drive_id in context/cards/library.yaml"
  else
    die "walk failed. Read the error above:
   'requires a quota project' / PERMISSION_DENIED → gcloud auth application-default set-quota-project \$MRLOAD_BQ_PROJECT
                                                   && gcloud services enable drive.googleapis.com
   'invalid_scope' / 'Access blocked' / insufficient scopes → gcloud auth login --enable-gdrive-access --update-adc
   'accessNotConfigured' → gcloud services enable drive.googleapis.com --project=\$MRLOAD_BQ_PROJECT
   Or run the whole sequence once:  scripts/gauth.sh"
  fi

  say "0/preflight — HubSpot token (read-only probe)"
  if [[ -n "${HUBSPOT_SANDBOX_TOKEN:-}" ]]; then
    local info; info=$(curl -sS -H "Authorization: Bearer $HUBSPOT_SANDBOX_TOKEN" "$HS_BASE/account-info/v3/details" || true)
    local pid; pid=$($PY -c "import json,sys;print(json.loads(sys.argv[1]).get('portalId','?'))" "$info" 2>/dev/null || echo "?")
    [[ "$pid" != "?" ]] || die "HubSpot token rejected: $info"
    if [[ "$pid" == "9201667" && "${MRLOAD_ALLOW_PROD_PORTAL:-0}" != "1" ]]; then
      die "token resolves to portal 9201667 (WISeKey SA / ICALPS PRODUCTION). Sandbox first. Set MRLOAD_ALLOW_PROD_PORTAL=1 only when production is the deliberate target."
    fi
    [[ -n "${HUBSPOT_SANDBOX_PORTAL_ID:-}" && "$HUBSPOT_SANDBOX_PORTAL_ID" != "00000000" && "$HUBSPOT_SANDBOX_PORTAL_ID" != "$pid" ]] && \
      die "token resolves to portal $pid but HUBSPOT_SANDBOX_PORTAL_ID=$HUBSPOT_SANDBOX_PORTAL_ID — wrong token or wrong portal id in .env"
    ok "token valid for portal $pid  ← OPERATOR: confirm this is the SANDBOX"
  fi

  say "0/preflight — BigQuery access"
  bq --location="$LOCATION" --project_id="$MRLOAD_BQ_PROJECT" ls >/dev/null && ok "bq reachable on $MRLOAD_BQ_PROJECT"
  ok "preflight complete"
}

step_walk() {
  say "0/walk — Drive API DFS of the scope root (30 Sales / 20 opportunities and customer data)"
  logrun walk $RUNNER walk --hierarchy-out "$HIER" --silver-preview-out "$SILVER" >"$STATE/walk.json"
  cat "$STATE/walk.json"
  echo
  echo "   OPERATOR checks: company_folders ≈ number of customer folders you expect under each segment;"
  echo "   multi_parent_nodes and duplicate_node_keys should be 0 (else dbt test will STOP on them);"
  echo "   pruned counts the tradeshow subtrees skipped."
  ok "hierarchy → $HIER ; silver preview → $SILVER"
}

step_bq_init() {
  say "B/bq-init — datasets + empty ledger tables so dbt sources resolve before step 6"
  for ds in "$RAW_DS" "$SILVER_DS"; do
    bq --project_id="$MRLOAD_BQ_PROJECT" ls -d 2>/dev/null | grep -qw "$ds" \
      || bq --location="$LOCATION" --project_id="$MRLOAD_BQ_PROJECT" mk --dataset "$ds"
    ok "dataset $ds"
  done
  for t in companies_resolved files_uploaded file_notes_posted deals_created; do
    bq --project_id="$MRLOAD_BQ_PROJECT" show "$RAW_DS.$t" >/dev/null 2>&1 \
      || bq --project_id="$MRLOAD_BQ_PROJECT" mk --table "$RAW_DS.$t" "pipeline/library_files/sql/ledger/$t.schema.json"
    ok "table $RAW_DS.$t"
  done
}

step_bq_load() {
  say "1a/bq-load — hierarchy CSV → $RAW_DS.library_hierarchy (creates the table on first run)"
  [[ -f "$HIER" ]] || die "run 'walk' first"
  $RUNNER bq-load --dataset "$RAW_DS" --source-uri "$HIER" --replace   # prints the command (dry)
  confirm "Load $(wc -l <"$HIER") lines into $RAW_DS.library_hierarchy?"
  MRLOAD_APPROVE_BQ_LOAD=1 logrun bq-load $RUNNER bq-load --dataset "$RAW_DS" --source-uri "$HIER" --replace
  ok "bronze loaded"
}

step_dbt() {
  say "2a+3a/dbt — silver build + the cardinality contract"
  need dbt "pip install dbt-bigquery"
  ( cd dbt && { [[ -d dbt_packages/dbt_utils ]] || logrun dbt-deps dbt deps --profiles-dir . --target "$DBT_TARGET"; } \
           && logrun dbt-run  dbt run  --profiles-dir . --target "$DBT_TARGET" \
           && { logrun dbt-docs dbt docs generate --profiles-dir . --target "$DBT_TARGET" >/dev/null \
                || warn "dbt docs generate failed — hs-props-verify will check HubSpot definitions only"; } \
           && logrun dbt-test dbt test --profiles-dir . --target "$DBT_TARGET" )
  # docs generate sits between run and test on purpose: it rewrites target/run_results.json, and the
  # gate (and scripts/dev/dbt_failures.sh) must see the TEST results there; catalog.json feeds hs-props-verify.
  echo
  echo "   OPERATOR reads: any FAIL = cardinality violation (multi-parent file, duplicate company name in a"
  echo "   segment, tradeshow leak, deal_candidate that is not a PO/Billing pdf). Fix in Drive, re-walk, re-load."
  echo "   WARN on assert_asset_has_company_anchor = loose files at segment level → silver_library_orphans."
  ok "cardinality gate green — company creation may proceed"
}

step_hs_props() {
  say "P/hs-props — HubSpot property definitions for the library index (the StackSync targets)"
  [[ -n "${HUBSPOT_SANDBOX_TOKEN:-}" ]] || die "HUBSPOT_SANDBOX_TOKEN unset — definitions are created in that portal"
  logrun hs-props-dry $RUNNER properties ensure >"$STATE/hs_props_dry.json" || true
  props_summary "$STATE/hs_props_dry.json"
  local mm; mm=$(count_json "$STATE/hs_props_dry.json" "sum(1 for r in d if r['status']=='type_mismatch')")
  [[ "$mm" == "0" ]] || die "$mm existing definitions differ in type from context/cards/library.yaml — rename in the card or fix in HubSpot; nothing is modified automatically"
  local n; n=$(count_json "$STATE/hs_props_dry.json" "sum(1 for r in d if r['status']=='would_create')")
  if [[ "$n" == "0" ]]; then ok "every declared group/property already exists in the portal"; return 0; fi
  confirm "Create $n property groups/definitions in the portal of HUBSPOT_SANDBOX_TOKEN (definitions only, no values)?"
  MRLOAD_APPROVE_PROPERTY_CREATE=1 logrun hs-props-live $RUNNER properties ensure >"$STATE/hs_props_live.json" || true
  props_summary "$STATE/hs_props_live.json"
  local f; f=$(count_json "$STATE/hs_props_live.json" "sum(1 for r in d if r['status']=='failed')")
  [[ "$f" == "0" ]] || die "$f definitions failed — a 403 names the missing scope (schema write for that object type); fix the private app and re-run"
  ok "property definitions in place — StackSync can now be mapped to them (after hs-props-verify)"
}
props_summary() {  # props_summary <json>
  $PY - "$1" <<'EOF'
import json, sys, collections
d = json.load(open(sys.argv[1])); c = collections.Counter((r["object_type"], r["status"]) for r in d)
for (o, s), n in sorted(c.items()): print(f"  {o:<10} {s:<16} {n}")
for r in d:
    if r["status"] in ("type_mismatch", "failed"): print("  !!", r["object_type"], r.get("name") or r.get("hubspot_property"), r["status"], r.get("error") or r.get("note"))
EOF
}

step_hs_props_verify() {
  say "P'/hs-props-verify — definitions vs the silver models as built → $REVIEW/stacksync_mapping.csv"
  [[ -f dbt/target/catalog.json ]] || warn "dbt/target/catalog.json missing (run 'dbt' first) — HubSpot definitions checked, silver columns not"
  logrun hs-props-verify $RUNNER properties verify --catalog dbt/target/catalog.json --out-dir "$REVIEW" >"$STATE/hs_props_verify.json" || true
  props_summary "$STATE/hs_props_verify.json"
  local bad; bad=$(count_json "$STATE/hs_props_verify.json" "sum(1 for r in d if r['status'] not in ('ok','ok_hubspot_only'))")
  [[ "$bad" == "0" ]] || die "$bad rows not ok (missing → run hs-props; column_missing → the card maps a column the silver model does not have)"
  echo
  echo "   OPERATOR (StackSync UI) — one sync per object type, direction BigQuery → HubSpot, from $REVIEW/stacksync_mapping.csv:"
  echo "     source      = bigquery_table (silver model as built)"
  echo "     destination = HubSpot <object_type>"
  echo "     match key   = the match_key row: bigquery_column  ↔  HubSpot Record ID (hs_object_id)"
  echo "     fields      = every property row: bigquery_column → hubspot_property (types already aligned)"
  echo "   Values appear in HubSpot only after ledger-export has written the record ids back (hs_company_id, hs_note_id, hs_deal_id)."
  ok "mapping sheet written"
}

step_review() {
  say "review — operator queues (offline)"
  [[ -f "$HIER" ]] || die "run 'walk' first"
  logrun review $RUNNER review-export --hierarchy "$HIER" --out-dir "$REVIEW"
  echo
  echo "   OPERATOR: open $REVIEW/"
  echo "     companies.csv          → the company objects that will be searched/created (names = folder names)"
  echo "     deal_candidates.csv    → PO / Billing PDFs (pass 2)"
  echo "     parked_for_review.csv  → other PDFs awaiting your review"
  echo "     orphans.csv            → files without a company folder (never attached)"
  echo "     multi_parent.csv       → files linked into 2+ folders (must be 0 before going live)"
  echo "     deal_decisions.csv     → edit approve=Y, dealname, pipeline, dealstage, amount for pass 2"
}

step_companies_dry() {
  say "1b/companies-dry — exact-name search against HubSpot, no creation"
  logrun companies-dry $RUNNER companies --hierarchy "$HIER" --ledger "$LEDGER" >"$STATE/companies_dry.json" || true
  $PY - "$STATE/companies_dry.json" <<'EOF'
import json, sys, collections
d = json.load(open(sys.argv[1])); c = collections.Counter(r["status"] for r in d)
print(dict(c))
for r in d:
    if r["status"] in ("ambiguous_match", "failed"): print("  !!", r["company_name"], r["status"], r.get("error"))
EOF
  echo "   OPERATOR: matched_by_name = existing companies reused; would_create = new companies;"
  echo "   ambiguous_match = several companies share the folder name → resolve in HubSpot (merge/rename) before live."
}

step_companies_live() {
  step_companies_dry
  local n; n=$(count_json "$STATE/companies_dry.json" "sum(1 for r in d if r['status']=='would_create')")
  local amb; amb=$(count_json "$STATE/companies_dry.json" "sum(1 for r in d if r['status']=='ambiguous_match')")
  [[ "$amb" == "0" ]] || die "$amb ambiguous company names — resolve them in HubSpot first"
  say "2b/companies-live — create $n companies"
  confirm "Create $n HubSpot companies (portal of HUBSPOT_SANDBOX_TOKEN)?"
  MRLOAD_APPROVE_COMPANY_CREATE=1 logrun companies-live $RUNNER companies --hierarchy "$HIER" --ledger "$LEDGER" >"$STATE/companies_live.json" || true
  ledger_sql "select status, count(*) from companies_resolved group by status"
  ok "ledger.companies_resolved populated"
}

step_attach_dry() {
  say "3b/attach-dry — attachable rows (files under resolved companies), nothing fired"
  logrun attach-dry $RUNNER attach --hierarchy "$HIER" --ledger "$LEDGER" >"$STATE/attach_dry.json" || true
  $PY -c "import json;d=json.load(open('$STATE/attach_dry.json'));print(len(d),'rows would be uploaded+attached')"
}

step_attach_upload() {
  step_attach_dry
  say "4b/attach-upload — phase 1: download from Drive on demand → POST /files/v3/files"
  confirm "Upload $(count_json "$STATE/attach_dry.json" 'len(d)') files to HubSpot Files (/mrload_library, PRIVATE)?"
  MRLOAD_APPROVE_FILES_UPLOAD=1 logrun attach-upload $RUNNER attach --hierarchy "$HIER" --ledger "$LEDGER" >"$STATE/attach_upload.json" || true
  ledger_sql "select status, count(*) from files_uploaded group by status"
  echo "   OPERATOR: status=uploaded rows carry hs_file_id; failed rows list the error — re-run this step, it skips uploaded rows."
}

step_attach_notes() {
  say "5b/attach-notes — phase 2: note (hs_attachment_ids) + default association note → company"
  local up; up=$(ledger_sql "select count(*) from files_uploaded where status='uploaded'")
  confirm "Create notes for $up uploaded files and associate them to their company?"
  MRLOAD_APPROVE_FILES_UPLOAD=1 MRLOAD_APPROVE_FILE_NOTES_POST=1 \
    logrun attach-notes $RUNNER attach --hierarchy "$HIER" --ledger "$LEDGER" >"$STATE/attach_notes.json" || true
  ledger_sql "select status, count(*) from file_notes_posted group by status"
  echo "   OPERATOR: open one company record in HubSpot → Activities → Notes: the file appears as a note attachment."
  echo "   partial = note created but association failed (re-run converges); rollback = scripts/run_pass1.sh unmigrate"
}

step_ledger_export() {
  say "6/ledger-export — HubSpot ids → BigQuery (mrload_raw.*) → dbt build"
  $RUNNER ledger-export --ledger "$LEDGER" --out-dir "$STATE/ledger_export" --dataset "$RAW_DS"
  confirm "Load the 4 ledger tables into $RAW_DS and rebuild silver?"
  MRLOAD_APPROVE_BQ_LOAD=1 logrun ledger-export $RUNNER ledger-export --ledger "$LEDGER" --out-dir "$STATE/ledger_export" --dataset "$RAW_DS"
  ( cd dbt && logrun dbt-build dbt build --profiles-dir . --target "$DBT_TARGET" )
  echo "   Result: silver_library_company.hs_company_id, silver_library_index.hs_file_id/hs_note_id now populated"
  echo "   → the associativity layer can join on them."
}

step_deals_dry() {
  say "7/deals-dry — pass 2 from $REVIEW/deal_decisions.csv (approve=Y rows only)"
  [[ -f "$REVIEW/deal_decisions.csv" ]] || die "run 'review' and edit deal_decisions.csv first"
  logrun deals-dry $RUNNER deals --decisions "$REVIEW/deal_decisions.csv" --ledger "$LEDGER" >"$STATE/deals_dry.json" || true
  $PY -c "import json,collections;d=json.load(open('$STATE/deals_dry.json'));print(dict(collections.Counter(r['status'] for r in d)))"
  echo "   OPERATOR: would_create = approved rows with dealname+dealstage; no_company_resolved = run companies-live first;"
  echo "   set MRLOAD_DEAL_PIPELINE / MRLOAD_DEAL_STAGE (portal ids) or fill them per row."
}

step_deals_live() {
  step_deals_dry
  local n; n=$(count_json "$STATE/deals_dry.json" "sum(1 for r in d if r['status']=='would_create')")
  confirm "Create $n deals, associate deal → company and note → deal?"
  MRLOAD_APPROVE_DEAL_CREATE=1 logrun deals-live $RUNNER deals --decisions "$REVIEW/deal_decisions.csv" --ledger "$LEDGER" >"$STATE/deals_live.json" || true
  ledger_sql "select status, count(*) from deals_created group by status"
  echo "   Then re-run: scripts/run_pass1.sh ledger-export   (hs_deal_id → silver_library_deal_candidates)"
}

step_unmigrate() {
  say "rollback — delete every note attached by this pipeline (ledger is the index)"
  $RUNNER unmigrate --ledger "$LEDGER" | head -20
  confirm "DELETE the attached notes listed above from HubSpot?"
  MRLOAD_APPROVE_UNMIGRATE=1 logrun unmigrate $RUNNER unmigrate --ledger "$LEDGER"
}

step_status() {
  say "status"
  for f in "$HIER" "$SILVER" "$REVIEW/deal_decisions.csv" "$LEDGER"; do
    [[ -e "$f" ]] && ok "$f" || warn "missing $f"
  done
  [[ -f "$LEDGER" ]] && for t in companies_resolved files_uploaded file_notes_posted deals_created; do
    echo "  $t: $(ledger_sql "select status, count(*) from $t group by status")"
  done
}

step_all() {
  step_preflight; step_walk; step_bq_init; step_bq_load; step_hs_props; step_dbt; step_hs_props_verify; step_review
  step_companies_live; step_attach_upload; step_attach_notes; step_ledger_export
  step_status
  echo; ok "pass 1 complete. Pass 2: edit $REVIEW/deal_decisions.csv then 'deals-dry' / 'deals-live'."
}

case "${1:-}" in
  preflight) step_preflight ;;      walk) step_walk ;;
  bq-init) step_bq_init ;;          bq-load) step_bq_load ;;
  dbt) step_dbt ;;                  review) step_review ;;
  hs-props) step_hs_props ;;        hs-props-verify) step_hs_props_verify ;;
  companies-dry) step_companies_dry ;; companies-live) step_companies_live ;;
  attach-dry) step_attach_dry ;;    attach-upload) step_attach_upload ;;
  attach-notes) step_attach_notes ;; ledger-export) step_ledger_export ;;
  deals-dry) step_deals_dry ;;      deals-live) step_deals_live ;;
  unmigrate) step_unmigrate ;;      status) step_status ;;
  all) step_all ;;
  *) sed -n '2,32p' "$0"; exit 2 ;;
esac
