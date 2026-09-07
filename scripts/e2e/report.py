#!/usr/bin/env python3
"""Cross-checks the rehearsal artefacts and writes REPORT.md.

Sources of truth compared against each other:
  hierarchy CSV (walker)  ·  SQLite ledger (pipeline belief)
  HubSpot mock /__state   ·  bq stub state  ·  dbt run_results.json  ·  DuckDB silver tables
"""
import csv, json, sqlite3, sys, urllib.request
from pathlib import Path

R = Path(sys.argv[1]); HS = sys.argv[2]
checks: list[tuple[str, bool, str]] = []


def check(name, ok, detail=""):
    checks.append((name, bool(ok), detail))


def jload(p):
    return json.loads(Path(p).read_text())


hier = list(csv.DictReader((R / "library_hierarchy.csv").open(encoding="utf-8")))
files = [r for r in hier if r["is_dir"] == "False"]
attachable = [r for r in files if r["company_node_key"] and r["asset_class"] != "shortcut"]
companies = [r for r in hier if r["libr_category"] == "company_folder"]
deal_cands = [r for r in files if r["asset_class"] == "deal_candidate"]
company_of = {r["node_key"]: r["node_name"] for r in companies}

# 1. walk
check("walk: tradeshow subtree pruned", not any(r["rel_path"].startswith("70 Tradeshows") for r in hier),
      f"{len(hier)} nodes")
check("walk: 7 company folders (6 Quantum + 1 Photonics)", len(companies) == 7, str(len(companies)))
check("walk: 4 deal candidates (PO/Billing PDFs, case-insensitive, any depth)", len(deal_cands) == 4,
      ", ".join(r["node_name"] for r in deal_cands))
check("walk: shortcut classified and excluded from attach", any(r["asset_class"] == "shortcut" for r in files)
      and not any(r["asset_class"] == "shortcut" for r in attachable))
check("walk: orphan at segment level has no company anchor",
      any(r["node_name"].startswith("251226") and not r["company_node_key"] for r in files))
check("walk: no multi-parent / duplicate keys (clean scenario)",
      all((r["parents_count"] or "1") == "1" for r in hier) and len({r["node_key"] for r in hier}) == len(hier))
check("walk: every attachable file anchors to an existing company folder",
      all(r["company_node_key"] in company_of for r in attachable), f"{len(attachable)} attachable")

# 2. bq stub
bq = jload(R / "bqstub" / "state.json")
check("bq: library_hierarchy load validated positionally + by type, row count == CSV",
      bq["tables"].get("mrload_raw.library_hierarchy", {}).get("rows") == len(hier) and not bq["errors"],
      f"errors={bq['errors']}")
check("bq: 4 ledger tables loaded (step 6)", all(f"mrload_raw.{t}" in bq["tables"] for t in
      ("companies_resolved", "files_uploaded", "file_notes_posted", "deals_created")))

# 3. dbt (first pass = the cardinality gate)
def dbt_summary(p):
    rr = jload(p)["results"]
    tests = [r for r in rr if r["unique_id"].startswith("test.")]
    return {s: sum(1 for r in tests if r["status"] == s) for s in ("pass", "warn", "fail", "error")}, len(tests)

s1, n1 = dbt_summary(R / "dbt_test_gate.json")
check(f"dbt gate: {n1} tests, 0 fail/error", s1["fail"] == 0 and s1["error"] == 0 and n1 >= 30, str(s1))
check("dbt gate: orphan check surfaces as WARN (not STOP)", s1["warn"] >= 1, str(s1))

# 4. ledger vs HubSpot mock
con = sqlite3.connect(R / "ledger.sqlite")
q = lambda sql: con.execute(sql).fetchall()
hs = json.load(urllib.request.urlopen(f"{HS}/__state"))
comp_status = dict(q("select status, count(*) from companies_resolved group by status"))
check("companies: Thorlabs matched by name (pre-existing), 6 created", comp_status.get("matched_by_name") == 1
      and comp_status.get("created") == 6 and hs["requests"]["company_create"] == 6, str(comp_status))
ledger_companies = dict(q("select company_node_key, hs_company_id from companies_resolved where hs_company_id is not null"))
check("companies: every ledger hs_company_id exists in HubSpot", all(v in hs["companies"] for v in ledger_companies.values()))

up = dict(q("select status, count(*) from files_uploaded group by status"))
check(f"attach p1: {len(attachable)} files uploaded, none failed", up.get("uploaded") == len(attachable) and not up.get("failed"),
      str(up))
check("attach p1: 429 retried once (uploads requested == files + 1)", hs["requests"]["upload"] == len(hs["files"]) + 1
      and len(hs["files"]) == len(attachable), f"requests={hs['requests']['upload']} files={len(hs['files'])}")
check("attach p1: native Google docs exported before upload",
      all(any(f["name"] == r["node_name"] for f in hs["files"].values()) for r in attachable
          if r["drive_mimetype"].startswith("application/vnd.google-apps")))
at = dict(q("select status, count(*) from file_notes_posted group by status"))
check(f"attach p2: {len(attachable)} notes attached, none partial/failed", at.get("attached") == len(attachable)
      and not at.get("partial") and not at.get("failed"), str(at))
note_assoc = [a for a in hs["associations"] if a["from"].startswith("note:") and a["to"].startswith("company:")]
check("attach p2: one note→company association per note", len(note_assoc) == len(hs["notes"]) == len(attachable))

# cardinality: each note landed on the company its file's folder anchors to
note_by_legacy = dict(q("select legacy_library_id, hs_note_id from file_notes_posted where status='attached'"))
assoc_to = {a["from"].split(":")[1]: a["to"].split(":")[1] for a in note_assoc}
mismatch = [r["node_name"] for r in attachable
            if assoc_to.get(note_by_legacy.get(r["legacy_library_id"])) != ledger_companies.get(r["company_node_key"])]
check("cardinality: every note is associated to exactly its folder's company (Library→Company N:1)",
      not mismatch, f"mismatches={mismatch[:5]}")

# 5. idempotency
a1, a2 = jload(R / "hs_after_attach.json"), jload(R / "hs_after_rerun.json")
check("idempotency: re-running attach fired zero new uploads/notes/associations",
      a1["requests"] == a2["requests"] and len(a1["notes"]) == len(a2["notes"]))

# 6. step 6 write-back visible in silver (DuckDB)
try:
    import duckdb
    d = duckdb.connect(str(R / "rehearsal.duckdb"), read_only=True)
    n_idx = d.execute("select count(*) from main.silver_library_index").fetchone()[0]
    n_note = d.execute("select count(*) from main.silver_library_index where hs_note_id is not null").fetchone()[0]
    n_co = d.execute("select count(*) from main.silver_library_company where hs_company_id is not null").fetchone()[0]
    n_deal = d.execute("select count(*) from main.silver_library_deal_candidates where hs_deal_id is not null").fetchone()[0]
    n_orph = d.execute("select count(*) from main.silver_library_orphans").fetchone()[0]
    check("silver: index rows == attachable files", n_idx == len(attachable), f"{n_idx}")
    check("silver: hs_note_id populated for every index row after ledger-export", n_note == n_idx, f"{n_note}/{n_idx}")
    check("silver: hs_company_id populated for all 7 companies", n_co == 7, str(n_co))
    check("silver: orphans model holds the segment-level spreadsheet", n_orph == 1, str(n_orph))
except Exception as exc:  # pragma: no cover
    check("silver: DuckDB tables readable", False, str(exc)); n_deal = None

# 7. pass 2
dl = dict(q("select status, count(*) from deals_created group by status"))
deal_assoc_co = [a for a in hs["associations"] if a["from"].startswith("deal:") and a["to"].startswith("company:")]
note_assoc_deal = [a for a in hs["associations"] if a["from"].startswith("note:") and a["to"].startswith("deal:")]
check("pass 2: 4 deals created from approved decisions", dl.get("created") == 4 and len(hs["deals"]) == 4, str(dl))
check("pass 2: deal→company and note→deal associations per deal",
      len(deal_assoc_co) == 4 and len(note_assoc_deal) == 4)
check("pass 2: hs_deal_id visible in silver_library_deal_candidates", n_deal == 4, str(n_deal))

s2, n2 = dbt_summary(R / "dbt_build_final.json")
check(f"dbt final build after write-back: {n2} tests, 0 fail/error", s2["fail"] == 0 and s2["error"] == 0, str(s2))

ok = sum(1 for _, o, _ in checks if o)
lines = [f"# mr-load e2e rehearsal — {ok}/{len(checks)} checks passed\n",
         "Real code paths: `googleapiclient` walker → Drive mock · `requests` HubSpot client → HubSpot mock · "
         "`bq` stub with schema validation · real dbt models + tests on DuckDB · SQLite ledger.\n",
         "| # | check | result | detail |", "|---|---|---|---|"]
for i, (n, o, dtl) in enumerate(checks, 1):
    lines.append(f"| {i} | {n} | {'PASS' if o else 'FAIL'} | {dtl} |")
(R / "REPORT.md").write_text("\n".join(lines) + "\n")
print("\n".join(lines))
sys.exit(0 if ok == len(checks) else 1)
