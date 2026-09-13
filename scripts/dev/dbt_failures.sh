#!/usr/bin/env bash
# Prints every non-passing node from the last dbt run/test (dbt/target/run_results.json) with its compiled SQL path.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; cd "$REPO_ROOT"
RR="dbt/target/run_results.json"
[[ -f "$RR" ]] || { echo "no $RR yet — run scripts/run_pass1.sh dbt (or the rehearsal) first"; exit 1; }
python3 - "$RR" <<'PY'
import json, sys, glob
r = json.load(open(sys.argv[1])); bad = [x for x in r["results"] if x["status"] not in ("pass", "success")]
print(f"dbt {r['args'].get('which','?')} at {r['metadata']['generated_at']}: {len(r['results'])} nodes, {len(bad)} not passing")
for x in bad:
    uid = x["unique_id"]; name = uid.split(".")[-1]
    sql = glob.glob(f"dbt/target/compiled/**/{name}.sql", recursive=True)
    print(f"  {x['status']:<6} {uid}  failures={x.get('failures')}  {(x.get('message') or '')[:100]}")
    for s in sql: print(f"         compiled: {s}")
sys.exit(1 if bad else 0)
PY
