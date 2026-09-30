#!/usr/bin/env python3
"""Cross-checks the rehearsal artefacts and writes REPORT.md.

Sources of truth compared against each other:
  hierarchy CSV (walker)  ·  SQLite ledger (pipeline belief)
  HubSpot mock /__state   ·  bq stub state  ·  dbt run_results.json  ·  DuckDB silver tables
"""
import csv, filecmp, json, sqlite3, sys, urllib.request
from pathlib import Path

R = Path(sys.argv[1]); HS = sys.argv[2]
checks: list[tuple[str, bool, str]] = []


def check(name, ok, detail=""):
    checks.append((name, bool(ok), detail))


def jload(p):
    return json.loads(Path(p).read_text())


hier = list(csv.DictReader((R / "library_hierarchy.csv").open(encoding="utf-8")))
files = [r for r in hier if r["is_dir"] == "False"]
companies = [r for r in hier if r["libr_category"] == "company_folder"]
company_of = {r["node_key"]: r["node_name"] for r in companies}
# pass 1 attaches (and silver indexes) only files anchored to a COMPANY FOLDER; files under a year-prefixed
# company-level folder (2021_ELTA, 2022_Aselsan) carry a self-anchored key that is not a company folder
attachable = [r for r in files if r["company_node_key"] in company_of and r["asset_class"] != "shortcut"]
engagement_files = [r for r in files if r["company_node_key"] and r["company_node_key"] not in company_of and r["asset_class"] != "shortcut"]
deal_cands = [r for r in files if r["asset_class"] == "deal_candidate"]

# 1. walk
check("walk: tradeshow subtree pruned", not any(r["rel_path"].startswith("70 Tradeshows") for r in hier),
      f"{len(hier)} nodes")
check("walk: 7 company folders (6 Quantum + 1 Photonics)", len(companies) == 7, str(len(companies)))
check("walk: 7 deal candidates (PO/Billing PDFs, case-insensitive, any depth)", len(deal_cands) == 7,
      ", ".join(r["node_name"] for r in deal_cands))
# deal layer, structural half (walker): level-3 key set on the first folder under a company and inherited
by_path = {r["rel_path"]: r for r in hier}
rfq = by_path.get("Quantum/Toshiba/2026 Quantum sensor RFQ", {})
check("walk: deal_node_key = self on the level-3 folder, inherited by the level-4 file, absent directly under the company",
      rfq.get("deal_node_key") == rfq.get("node_key")
      and by_path["Quantum/Toshiba/2026 Quantum sensor RFQ/drawings/layout.gds"]["deal_node_key"] == rfq.get("node_key")
      and by_path["Quantum/Toshiba/Toshiba PO 2026-001.pdf"]["deal_node_key"] == "")
check("walk: shortcut classified and excluded from attach", any(r["asset_class"] == "shortcut" for r in files)
      and not any(r["asset_class"] == "shortcut" for r in attachable))
check("walk: orphan at segment level has no company anchor",
      any(r["node_name"].startswith("251226") and not r["company_node_key"] for r in files))
check("walk: no multi-parent / duplicate keys (clean scenario)",
      all((r["parents_count"] or "1") == "1" for r in hier) and len({r["node_key"] for r in hier}) == len(hier))
check("walk: 4 files under year-prefixed company-level folders (2021_ELTA, 2022_Aselsan) are self-anchored to a non-company row: not attached, not indexed, salvaged in pass 2",
      len(engagement_files) == 4 and all(r["rel_path"].startswith(("Quantum/2021_ELTA", "Quantum/2022_Aselsan")) for r in engagement_files),
      str(len(engagement_files)))

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

# 5b. schema propagation: property definitions (before dbt) + mapping sheet (after dbt)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from pipeline.library_files.card import load_library_card      # noqa: E402
from pipeline.library_files.properties import load_property_plan  # noqa: E402
plan = load_property_plan(load_library_card().raw)
p1, p2 = jload(R / "hs_after_props.json"), jload(R / "hs_after_props_rerun.json")
declared = {(o.object_type, f.name) for o in plan.objects for f in o.fields}
present = {(o, n) for o, props in p1["properties"].items() for n in props}
check(f"properties: all {len(declared)} declared definitions exist in HubSpot after hs-props, in group {plan.group_name}",
      declared <= present and all(p1["properties"][o][n]["groupName"] == plan.group_name for o, n in declared),
      f"{len(declared & present)}/{len(declared)}")
check("properties: pre-existing companies.mrload_drive_link reused, never modified",
      p1["properties"]["companies"]["mrload_drive_link"]["label"] == "seeded label")
check("properties: second hs-props run created nothing (idempotent)",
      p1["requests"]["property_create"] == p2["requests"]["property_create"] > 0
      and p1["requests"]["group_create"] == p2["requests"]["group_create"] == 2, str(p2["requests"]["property_create"]))
sheet = list(csv.DictReader((R / "review" / "stacksync_mapping.csv").open(encoding="utf-8")))
check(f"properties verify: mapping sheet has {len(declared)} property rows + 3 match keys, every silver column found in the DuckDB catalog",
      len(sheet) == len(declared) + 3 and all(r["status"] == "ok" for r in sheet)
      and all(r["bigquery_type"] for r in sheet if r["kind"] == "property"),
      str({r["status"] for r in sheet}))

# 6. step 6 write-back visible in silver (DuckDB)
try:
    import duckdb
    d = duckdb.connect(str(R / "rehearsal.duckdb"), read_only=True)
    n_idx = d.execute("select count(*) from main.silver_library_index").fetchone()[0]
    n_note = d.execute("select count(*) from main.silver_library_index where hs_note_id is not null").fetchone()[0]
    n_co = d.execute("select count(*) from main.silver_library_company where hs_company_id is not null").fetchone()[0]
    n_deal = d.execute("select count(*) from main.silver_library_deal_candidates where hs_deal_id is not null").fetchone()[0]
    n_orph = d.execute("select count(*) from main.silver_library_orphans").fetchone()[0]
    deals = d.execute("select deal_name, anchor_kind, pdf_count, deal_candidate_count, asset_count from main.silver_library_deal order by 1").fetchall()
    deal_names = {r[0]: r for r in deals}
    n_with_deal = d.execute("select count(*) from main.silver_library_index where legacy_deal_id is not null").fetchone()[0]
    check("silver: index rows == attachable files", n_idx == len(attachable), f"{n_idx}")
    # deal layer, heuristic half (dbt): 2 folder anchors (PDF beneath) + 3 self-anchored PO/Billing PDFs; exhibition and PDF-less folders absent
    check("deal: silver_library_deal = 2 folder anchors + 3 file anchors",
          len(deals) == 5 and sorted(r[1] for r in deals) == ["file", "file", "file", "folder", "folder"],
          ", ".join(f"{r[0]}[{r[1]}]" for r in deals))
    check("deal: the RFQ folder qualifies with pdf=2, PO/Billing=1, files=4; Submissions qualifies via its Billing PDF",
          deal_names.get("2026 Quantum sensor RFQ", ())[1:] == ("folder", 2, 1, 4) and deal_names.get("Submissions", ())[1:2] == ("folder",))
    check("deal: exhibition folder (excluded realm) and PDF-less 'Site survey' are NOT deals",
          "2025 Photonics West Exhibition" not in deal_names and "Site survey" not in deal_names)
    check("deal: legacy_deal_id set on the 6 files beneath the two folder anchors + the 3 self-anchored PDFs, NULL elsewhere",
          n_with_deal == 9, str(n_with_deal))
    py_anchors = list(csv.DictReader((R / "review" / "deal_anchors.csv").open(encoding="utf-8")))
    py_docs = list(csv.DictReader((R / "review" / "deal_documents.csv").open(encoding="utf-8")))
    py_company_anchors = [a for a in py_anchors if a["company_node_key"] in company_of]
    check("deal: Python qualifier agrees with the dbt model for company-folder anchors; the two orphan anchors (Tender, RFP) exist only on the Python side",
          sorted(a["deal_name"] for a in py_company_anchors) == sorted(deal_names)
          and {a["deal_name"] for a in py_anchors} - set(deal_names) == {"Tender", "RFP"}, str(len(py_anchors)))
    check("deal: 6 deferred documents beneath anchors (quotes, SOW, gds x2, offer) listed in review/deal_documents.csv, none of them PO/Billing",
          sorted(dd["node_name"] for dd in py_docs) == ["Quote QS-17.pdf", "SOW.docx", "layout.gds", "lnoi_MZI_tests.gds", "offer.pdf", "quote.pdf"]
          and all(dd["asset_class"] != "deal_candidate" for dd in py_docs), str(len(py_docs)))
    check("silver: hs_note_id populated for every index row after ledger-export", n_note == n_idx, f"{n_note}/{n_idx}")
    check("silver: hs_company_id populated for all 7 companies", n_co == 7, str(n_co))
    check("silver: orphans model holds the segment-level spreadsheet", n_orph == 1, str(n_orph))
except Exception as exc:  # pragma: no cover
    check("silver: DuckDB tables readable", False, str(exc)); n_deal = None

# 7. pass 2
dl = dict(q("select status, count(*) from deals_created group by status"))
deal_assoc_co = [a for a in hs["associations"] if a["from"].startswith("deal:") and a["to"].startswith("company:")]
note_assoc_deal = [a for a in hs["associations"] if a["from"].startswith("note:") and a["to"].startswith("deal:")]
check("pass 2: 6 deals created from 7 approved anchors (one per anchor, not per PDF; Aselsan's has no company)",
      dl.get("created") == 6 and len(hs["deals"]) == 6, str(dl))
check("pass 2: 6 deal→company associations; 5 note→deal (PO/Billing notes only; ELTA's PO was never attached — strict pass 1)",
      len(deal_assoc_co) == 6 and len(note_assoc_deal) == 5, f"{len(deal_assoc_co)}/{len(note_assoc_deal)}")
check("pass 2: hs_deal_id visible on the 5 indexed deal-candidate files through their anchor", n_deal == 5, str(n_deal))

# 7b. orphan salvage (pass 2 enrichment) + HubSpot import files + strict pass 1
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from pipeline.library_files.hubspot_import import bigquery_column_name, deal_import_columns  # noqa: E402
co_rows = {r[0]: r for r in q("select company_node_key, company_name, hs_company_id, status from companies_resolved")}
elta_key = next((k for k in co_rows if k.endswith("|2021_ELTA")), None)
asel_key = next((k for k in co_rows if k.endswith("|2022_Aselsan")), None)
elta_deal = [a for a in deal_assoc_co if a["to"] == "company:9002"]
check("salvage: ELTA (year-prefixed company folder) found by name in the portal → ledger matched_by_name_pass2 → its deal associated to company 9002, with 0 notes",
      elta_key is not None and co_rows[elta_key][1:] == ("ELTA", "9002", "matched_by_name_pass2") and len(elta_deal) == 1
      and not any(a["to"] == elta_deal[0]["from"] for a in note_assoc_deal), str(co_rows.get(elta_key)))
check("salvage: Aselsan not in the portal → ledger not_in_portal_pass2 (null id), no deal created, nothing created in HubSpot for it",
      asel_key is not None and co_rows[asel_key][1:] == ("Aselsan", None, "not_in_portal_pass2")
      and all(c["properties"]["name"] != "Aselsan" for c in hs["companies"].values()), str(co_rows.get(asel_key)))
attach_again = jload(R / "attach_after_salvage.json")
check("strict pass 1: an attach dry run after the salvage still lists only the company-folder files (ELTA's files stay unattached)",
      len(attach_again) == len(attachable), f"{len(attach_again)} vs {len(attachable)}")
imp = list(csv.DictReader((R / "review" / "hubspot_deals_import.csv").open(encoding="utf-8")))
ledger_deal_ids = {r[1] for r in q("select legacy_library_id, hs_deal_id from deals_created where hs_deal_id is not null")}
check("import: hubspot_deals_import.csv has one row per inferred anchor (7), header == the card-derived column set",
      len(imp) == 7 and list(imp[0].keys()) == deal_import_columns(load_library_card()), str(len(imp)))
check("import: Record ID filled from the ledger on the 6 API-created deals, blank on the Aselsan row; Pipeline / Deal Stage blank everywhere",
      {r["Record ID"] for r in imp if r["Record ID"]} == ledger_deal_ids and len(ledger_deal_ids) == 6
      and all(r["Pipeline"] == "" and r["Deal Stage"] == "" for r in imp)
      and next(r for r in imp if r["Company Name"] == "Aselsan")["Record ID"] == "")
check("import: Company Record ID filled on 6 rows (incl. ELTA → 9002); company status resolved×5 / salvaged×1 / missing_in_portal×1",
      sum(1 for r in imp if r["Company Record ID"]) == 6 and next(r for r in imp if r["Company Name"] == "ELTA")["Company Record ID"] == "9002"
      and sorted(r["Company status (mr-load)"] for r in imp) == ["missing_in_portal", "resolved", "resolved", "resolved", "resolved", "resolved", "salvaged"])
comp = list(csv.DictReader((R / "review" / "hubspot_companies_import.csv").open(encoding="utf-8")))
check("import: hubspot_companies_import.csv lists exactly the confirmed-missing company (Aselsan) with its Drive folder link",
      [c["Company name"] for c in comp] == ["Aselsan"] and comp[0]["Description"].startswith("Drive folder: https://"), str(comp[:1]))
# 7c. the same two files materialised in BigQuery by ledger-export (surface parity: console export → wizard)
bq_deals = bq["tables"].get("mrload_raw.hubspot_deals_import", {}); bq_comp = bq["tables"].get("mrload_raw.hubspot_companies_import", {})
check("bq: ledger-export loaded hubspot_deals_import (7 rows) + hubspot_companies_import (1 row) into mrload_raw with --replace, no load errors",
      bq_deals.get("rows") == 7 and bq_comp.get("rows") == 1 and bq_deals.get("replace") and bq_comp.get("replace") and not bq["errors"],
      f"{bq_deals.get('rows')}/{bq_comp.get('rows')} rows")
bq_schema = jload(R / "ledger_export" / "hubspot_deals_import.schema.json")
check("bq: the loaded deals table is byte-identical to review/hubspot_deals_import.csv; schema names = snake_case headers in CSV order (23, positional)",
      filecmp.cmp(R / "bqstub" / "mrload_raw.hubspot_deals_import.csv", R / "review" / "hubspot_deals_import.csv", shallow=False)
      and [f["name"] for f in bq_schema] == [bigquery_column_name(h) for h in deal_import_columns(load_library_card())] and len(bq_schema) == 23,
      f"{len(bq_schema)} columns, head {[f['name'] for f in bq_schema[:2]]}")

# 8. sequence bookkeeping: checkpoints + pipeline_state (what the run-sheet notebook reads)
ck = [l.split("\t") for l in (R / "checkpoints.tsv").read_text().splitlines()]
ran = [c[1] for c in ck if c[2] == "0"]
expected = ["preflight", "walk", "bq-init", "bq-load", "hs-props", "hs-props", "dbt", "hs-props-verify", "review",
            "companies-dry", "companies-live", "attach-dry", "attach-upload", "attach-notes", "attach-notes",
            "ledger-export", "deals-dry", "deals-live", "ledger-export"]   # live steps checkpoint their dry counterpart first
check("checkpoints: every step run recorded rc=0, in the executed order",
      ran == expected and all(c[2] == "0" for c in ck), " ".join(ran))
ps = jload(R / "pipeline_state.json")
check("pipeline_state: all 16 steps done, next = none (pass 1 + pass 2 complete, deal ids written back)",
      ps["next"] is None and all(s["status"] == "done" for s in ps["steps"]) and len(ps["steps"]) == 16,
      f"next={ps['next']} {ps['why']}")

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
