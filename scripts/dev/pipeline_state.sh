#!/usr/bin/env bash
# =============================================================================
# mr-load — where am I in the sequence, and what is next?
#   scripts/dev/pipeline_state.sh          # table + "next: <step>"
#   scripts/dev/pipeline_state.sh --json   # machine-readable (notebooks, tasks)
# Sources: $STATE/checkpoints.tsv (written by scripts/run_pass1.sh after every
# step run) + the artefacts each step leaves behind. A step is DONE only if its
# last successful run is newer than every predecessor's — re-running `walk`
# makes bq-load … ledger-export pending again (STALE), which is the point.
# =============================================================================
set -uo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; cd "$REPO_ROOT"
STATE="${MRLOAD_STATE_DIR:-.mrload}"
LEDGER="${MRLOAD_LEDGER_PATH:-$STATE/ledger.sqlite}"
JSON=0; [[ "${1:-}" == "--json" ]] && JSON=1
STATE="$STATE" LEDGER="$LEDGER" JSON="$JSON" python3 - <<'PY'
import csv, json, os, sqlite3
from pathlib import Path

STATE = Path(os.environ["STATE"]); LEDGER = Path(os.environ["LEDGER"]); JSON = os.environ["JSON"] == "1"
PASS1 = ["preflight", "walk", "bq-init", "bq-load", "hs-props", "dbt", "hs-props-verify", "review",
         "companies-dry", "companies-live", "attach-dry", "attach-upload", "attach-notes", "ledger-export"]
PASS2 = ["deals-dry", "deals-live"]
LIVE = {"bq-load", "hs-props", "companies-live", "attach-upload", "attach-notes", "ledger-export", "deals-live"}

# ── checkpoints ──────────────────────────────────────────────────────────────
last_ok, last_run = {}, {}
ck = STATE / "checkpoints.tsv"
if ck.exists():
    for line in ck.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) < 3: continue
        ts, step, rc = parts[0], parts[1], parts[2]; msg = parts[3] if len(parts) > 3 else ""
        last_run[step] = (ts, rc, msg)
        if rc == "0": last_ok[step] = ts

# ── evidence ─────────────────────────────────────────────────────────────────
def rows(p):
    try: return sum(1 for _ in csv.DictReader(Path(p).open(encoding="utf-8", newline="")))
    except Exception: return None
def jlen(p, expr=len):
    try: return expr(json.loads(Path(p).read_text(encoding="utf-8")))
    except Exception: return None
def counts(table):
    try:
        con = sqlite3.connect(f"file:{LEDGER}?mode=ro", uri=True)
        return dict(con.execute(f"select status, count(*) from {table} group by status").fetchall())
    except Exception: return None
def fmt(d): return " ".join(f"{k}={v}" for k, v in sorted(d.items())) if d else ""
def dbt_tests():
    try:
        r = json.loads(Path("dbt/target/run_results.json").read_text(encoding="utf-8"))
        if r.get("args", {}).get("which") not in ("test", "build"): return None
        st = {}
        for x in r["results"]:
            if x["unique_id"].startswith("test."): st[x["status"]] = st.get(x["status"], 0) + 1
        return st
    except Exception: return None
def status_summary(p):
    d = jlen(p, lambda d: d)
    if not isinstance(d, list): return None
    st = {}
    for r in d: st[r.get("status")] = st.get(r.get("status"), 0) + 1
    return st
def decisions_approved():
    p = STATE / "review" / "deal_decisions.csv"
    try: return sum(1 for r in csv.DictReader(p.open(encoding="utf-8", newline="")) if (r.get("approve") or "").strip().upper() == "Y")
    except Exception: return None

n_probe = rows(STATE / "preflight_probe.csv"); n_hier = rows(STATE / "library_hierarchy.csv")
ev = {
    "preflight": f"probe: {n_probe} depth-1 nodes" if n_probe is not None else "",
    "walk": f"{n_hier} nodes → library_hierarchy.csv" if n_hier is not None else "",
    "bq-init": "", "bq-load": f"{n_hier} rows loaded" if n_hier is not None and "bq-load" in last_ok else "",
    "hs-props": fmt(status_summary(STATE / "hs_props_live.json") or status_summary(STATE / "hs_props_dry.json")),
    "dbt": ("tests " + fmt(dbt_tests())) if ("dbt" in last_run and dbt_tests()) else "",   # dbt/target is repo-level: only meaningful after a dbt run here
    "hs-props-verify": (lambda n: f"{n} mapping rows → review/stacksync_mapping.csv" if n is not None else "")(rows(STATE / "review" / "stacksync_mapping.csv")),
    "review": (lambda n: f"{n} queue files" if n else "")(len(list((STATE / "review").glob("*.csv"))) if (STATE / "review").exists() else 0),
    "companies-dry": fmt(status_summary(STATE / "companies_dry.json")),
    "companies-live": "ledger " + fmt(counts("companies_resolved")) if counts("companies_resolved") else "",
    "attach-dry": (lambda n: f"{n} attachable rows" if n is not None else "")(jlen(STATE / "attach_dry.json")),
    "attach-upload": "ledger " + fmt(counts("files_uploaded")) if counts("files_uploaded") else "",
    "attach-notes": "ledger " + fmt(counts("file_notes_posted")) if counts("file_notes_posted") else "",
    "ledger-export": (lambda n: f"{n} ledger CSVs exported" if n else "")(len(list((STATE / "ledger_export").glob("*.csv"))) if (STATE / "ledger_export").exists() else 0),
    "deals-dry": fmt(status_summary(STATE / "deals_dry.json")),
    "deals-live": "ledger " + fmt(counts("deals_created")) if counts("deals_created") else "",
}

# ── status per step (order + staleness) ──────────────────────────────────────
order = PASS1 + PASS2
out, newest_before, newest_p1_for_p2 = [], "", ""
for i, step in enumerate(order, 1):
    ok = last_ok.get(step); run = last_run.get(step)
    if step == PASS2[0]:
        # pass 2 hangs off review + companies-live, not off the write-back: a later ledger-export
        # (run again after deals-live to push hs_deal_id) must not make the deals steps stale.
        newest_before = newest_p1_for_p2
    if ok and ok >= newest_before: status = "done"
    elif ok: status = "stale"
    elif run: status = "failed"
    else: status = "pending"
    if status == "done":
        newest_before = max(newest_before, ok)
        if step in PASS1 and step != "ledger-export": newest_p1_for_p2 = max(newest_p1_for_p2, ok)
    out.append({"n": i, "step": step, "live": step in LIVE, "status": status, "last_ok": ok,
                "last_rc": int(run[1]) if run else None, "message": (run[2] if run and run[1] != "0" else ""),
                "evidence": ev.get(step, "")})
by = {o["step"]: o for o in out}

# next: first pass-1 step not done; then pass 2 only when decisions are approved
nxt, why = None, ""
for o in out[:len(PASS1)]:
    if o["status"] != "done": nxt, why = o["step"], f"pass 1: {o['status']}"; break
if nxt is None:
    approved = decisions_approved() or 0
    if by["deals-live"]["status"] == "done" and last_ok.get("ledger-export", "") < last_ok["deals-live"]:
        nxt, why = "ledger-export", "write hs_deal_id back to BigQuery after deals-live"
    elif approved and by["deals-dry"]["status"] != "done":
        nxt, why = "deals-dry", f"pass 2: {approved} approved rows in review/deal_decisions.csv"
    elif approved and by["deals-live"]["status"] != "done":
        nxt, why = "deals-live", f"pass 2: {approved} approved rows"
    else:
        why = "pass 1 complete" + ("" if approved else "; pass 2 starts when you set approve=Y in review/deal_decisions.csv")

result = {"state_dir": str(STATE), "steps": out, "next": nxt, "why": why}
if JSON:
    print(json.dumps(result, indent=2)); raise SystemExit(0)
mark = {"done": "\033[1;32m✔ done   \033[0m", "stale": "\033[1;33m↻ stale  \033[0m", "failed": "\033[1;31m✖ failed \033[0m", "pending": "○ pending"}
print(f"mr-load pipeline state   (state dir {STATE}, checkpoints: {'yes' if ck.exists() else 'none yet'})")
print(f" {'#':>2}  {'step':<24} {'last ok (UTC)':<17} {'status':<9} evidence")
for o in out:
    if o["n"] == len(PASS1) + 1: print("     ── pass 2 (after you approve rows in review/deal_decisions.csv) ──")
    gate = " ⟨LIVE⟩" if o["live"] else ""
    line = f" {o['n']:>2}  {o['step'] + gate:<24} {(o['last_ok'] or '-'):<17} {mark[o['status']]} {o['evidence']}"
    if o["message"]: line += f"   ← {o['message'][:90]}"
    print(line)
print()
print(f"next: {nxt or '(none)'}   {why}")
PY
