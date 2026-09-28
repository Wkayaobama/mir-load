#!/usr/bin/env bash
# Peek into the StackSync mapping sheet written by `run_pass1.sh hs-props-verify`.
#   scripts/dev/mapping_sheet.sh          # counts per object/status + every row that is not ok, with its note
#   scripts/dev/mapping_sheet.sh --all    # every row
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; cd "$REPO_ROOT"
SHEET="${MRLOAD_STATE_DIR:-.mrload}/review/stacksync_mapping.csv"
[[ -f "$SHEET" ]] || { echo "no $SHEET yet — run scripts/run_pass1.sh hs-props-verify (after dbt)"; exit 1; }
ALL="${1:-}" SHEET="$SHEET" python3 - <<'PY'
import csv, collections, os
rows = list(csv.DictReader(open(os.environ["SHEET"], encoding="utf-8", newline="")))
c = collections.Counter((r["object_type"], r["status"]) for r in rows)
print(f"{os.environ['SHEET']}: {len(rows)} rows")
for (o, s), n in sorted(c.items()): print(f"  {o:<10} {s:<16} {n}")
hint = {"missing": "definition absent in the portal → scripts/run_pass1.sh hs-props (creates it; verify never does)",
        "type_mismatch": "portal has the name with another type → rename in the card or fix in HubSpot by hand",
        "column_missing": "the card maps a column the built silver model lacks → fix the card mapping, dbt, verify again",
        "unknown_no_token": "HUBSPOT_SANDBOX_TOKEN unset → set it, then hs-props / verify"}
for s, h in hint.items():
    if any(r["status"] == s for r in rows): print(f"  → {s}: {h}")
show = rows if os.environ["ALL"] == "--all" else [r for r in rows if r["status"] != "ok"]
if show:
    print()
    print(f"  {'object':<10} {'kind':<9} {'hubspot_property':<30} {'type':<9} {'bigquery_table.column':<52} {'status':<14} note")
    for r in show:
        print(f"  {r['object_type']:<10} {r['kind']:<9} {r['hubspot_property']:<30} {r['hubspot_type']:<9} "
              f"{(r['bigquery_table'].split('.')[-1] + '.' + r['bigquery_column']):<52} {r['status']:<14} {r['note'][:80]}")
PY
