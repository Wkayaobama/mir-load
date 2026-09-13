#!/usr/bin/env bash
# =============================================================================
# mr-load e2e rehearsal — the full scripted sequence (steps 0 → 7) with the
# real code against local stand-ins for the three external systems:
#   Drive v3 API  → tests/e2e/drive_mock.py   (real google-api-python-client)
#   HubSpot API   → tests/e2e/hubspot_mock.py (real requests client, 429 once)
#   BigQuery      → scripts/e2e/bin/bq stub (schema/type validation) + dbt on DuckDB
# No orchestrator: it drives scripts/run_pass1.sh step by step with --yes.
#   scripts/e2e_rehearsal.sh            → .mrload/rehearsal/REPORT.md
# =============================================================================
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$REPO_ROOT"
R="$REPO_ROOT/.mrload/rehearsal"; rm -rf "$R"; mkdir -p "$R"
P1=$((8700 + RANDOM % 200)); P2=$((P1 + 1))

python3 tests/e2e/drive_mock.py --port "$P1" --scenario "${SCENARIO:-clean}" >"$R/drive_mock.log" 2>&1 &
python3 tests/e2e/hubspot_mock.py --port "$P2" --fail-once upload >"$R/hubspot_mock.log" 2>&1 &
trap 'kill $(jobs -p) 2>/dev/null || true' EXIT
for i in $(seq 1 30); do
  curl -sf "http://127.0.0.1:$P1/__health" >/dev/null && curl -sf "http://127.0.0.1:$P2/__health" >/dev/null && break; sleep 0.3
done

export NO_PROXY="127.0.0.1,localhost,${NO_PROXY:-}" no_proxy="127.0.0.1,localhost"
export GOOGLE_APPLICATION_CREDENTIALS=""          # empty (not unset) so an operator .env cannot re-set it
export MRLOAD_DRIVE_API_BASE="http://127.0.0.1:$P1" MRLOAD_HUBSPOT_API_BASE="http://127.0.0.1:$P2"
export HUBSPOT_SANDBOX_TOKEN="mock-sandbox-token" HUBSPOT_SANDBOX_PORTAL_ID="424242" MRLOAD_BQ_PROJECT="rehearsal-project" MRLOAD_BQ_RAW_DATASET="mrload_raw" MRLOAD_BQ_DATASET="mrload"
export MRLOAD_STATE_DIR="$R" MRLOAD_LEDGER_PATH="$R/ledger.sqlite" MRLOAD_CACHE_DIR="$R/cache"
export BQSTUB_STATE="$R/bqstub/state.json" PATH="$REPO_ROOT/scripts/e2e/bin:$PATH"
export MRLOAD_DBT_TARGET=duckdb MRLOAD_DBT_CONTRACTS=false MRLOAD_DUCKDB_PATH="$R/rehearsal.duckdb"
export MRLOAD_HIERARCHY_CSV="$R/library_hierarchy.csv" MRLOAD_LEDGER_EXPORT_DIR="$R/ledger_export"
export MRLOAD_DEAL_PIPELINE="pipe-mock" MRLOAD_DEAL_STAGE="stage-mock" YES_ALL=1
RUN="scripts/run_pass1.sh"; RUNNER="python3 -m pipeline.library_files.runner"
snap() { curl -s "http://127.0.0.1:$P2/__state" >"$R/$1.json"; }

$RUN preflight
$RUN walk
$RUNNER ledger-export --ledger "$MRLOAD_LEDGER_PATH" --out-dir "$R/ledger_export" --dataset mrload_raw >/dev/null   # header-only CSVs for the DuckDB sources
$RUN bq-init
$RUN bq-load
$RUN hs-props;        snap hs_after_props
$RUN hs-props;        snap hs_after_props_rerun      # idempotent: 0 creates
$RUN dbt;             cp dbt/target/run_results.json "$R/dbt_test_gate.json"
$RUN hs-props-verify
$RUN review
$RUN companies-live
$RUN attach-upload
$RUN attach-notes;    snap hs_after_attach
$RUN attach-notes;    snap hs_after_rerun            # idempotency
$RUN ledger-export
python3 - "$R/review/deal_decisions.csv" <<'EOF'
import csv, sys
p = sys.argv[1]; rows = list(csv.DictReader(open(p, encoding="utf-8", newline="")))
for r in rows: r["approve"] = "Y"
w = csv.DictWriter(open(p, "w", encoding="utf-8", newline=""), fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)
print(f"approved {len(rows)} deal decisions")
EOF
$RUN deals-live
$RUN ledger-export;   cp dbt/target/run_results.json "$R/dbt_build_final.json"
$RUN status
scripts/dev/pipeline_state.sh; scripts/dev/pipeline_state.sh --json >"$R/pipeline_state.json"
python3 scripts/e2e/report.py "$R" "http://127.0.0.1:$P2"
