"""Pass 2 on the inferred anchor: approval gating, one deal per anchor, every PO/Billing note
beneath it associated, deferred documents untouched, idempotent re-run, ledger export."""
from __future__ import annotations

import csv
from pathlib import Path

from pipeline.library_files.deals import DECISION_COLUMNS, read_decisions, resolve_deals
from pipeline.library_files.ledger import LEDGER_TABLES, SqliteLedger


class FakeClient:
    def __init__(self, companies: dict[str, list[dict]] | None = None) -> None:
        self.calls: list[tuple] = []
        self._n = 500
        self.companies = companies or {}          # name → search hits (pass-2 salvage)

    def search_companies_by_name(self, name, *, limit=5):
        self.calls.append(("search", name))
        return list(self.companies.get(name, []))

    def create_deal(self, *, dealname, dealstage, pipeline=None, **extra):
        self._n += 1
        self.calls.append(("create_deal", dealname, dealstage, pipeline, extra))
        return {"id": str(self._n)}

    def associate_default(self, f, fid, t, tid):
        self.calls.append(("assoc", f, fid, t, tid))
        return {}


CO = "K|Quantum|Toshiba"; RFQ = "K|Quantum|Toshiba|2026 RFQ"
HIER = [
    {"node_key": CO, "node_name": "Toshiba", "libr_category": "company_folder", "asset_class": "",
     "company_node_key": "", "deal_node_key": "", "legacy_library_id": "3020Q-co", "is_dir": "True"},
    {"node_key": RFQ, "node_name": "2026 RFQ", "libr_category": "document", "asset_class": "",
     "company_node_key": CO, "deal_node_key": RFQ, "legacy_library_id": "3020QT-rfq", "is_dir": "True"},
    {"node_key": RFQ + "|PO_1.pdf", "node_name": "PO_1.pdf", "libr_category": "document", "asset_class": "deal_candidate", "extension": "pdf",
     "company_node_key": CO, "deal_node_key": RFQ, "legacy_library_id": "3020QT-po1", "is_dir": "False"},
    {"node_key": RFQ + "|Billing_2.pdf", "node_name": "Billing_2.pdf", "libr_category": "document", "asset_class": "deal_candidate", "extension": "pdf",
     "company_node_key": CO, "deal_node_key": RFQ, "legacy_library_id": "3020QT-bi2", "is_dir": "False"},
    {"node_key": RFQ + "|quote.pdf", "node_name": "quote.pdf", "libr_category": "document", "asset_class": "parked_for_review", "extension": "pdf",
     "company_node_key": CO, "deal_node_key": RFQ, "legacy_library_id": "3020QT-qu", "is_dir": "False"},
    {"node_key": CO + "|PO_solo.pdf", "node_name": "PO_solo.pdf", "libr_category": "document", "asset_class": "deal_candidate", "extension": "pdf",
     "company_node_key": CO, "deal_node_key": "", "legacy_library_id": "3020QT-solo", "is_dir": "False"},
]
# a year-prefixed folder at COMPANY level: self-anchored engagement_folder, never resolved by companies.py
ELTA = "K|Quantum|2021_ELTA"; TENDER = ELTA + "|Tender"
HIER_ELTA = HIER + [
    {"node_key": ELTA, "node_name": "2021_ELTA", "libr_category": "engagement_folder", "asset_class": "",
     "company_node_key": ELTA, "deal_node_key": "", "inferred_company_name": "ELTA", "legacy_library_id": "3020QE-co", "is_dir": "True"},
    {"node_key": TENDER, "node_name": "Tender", "libr_category": "document", "asset_class": "",
     "company_node_key": ELTA, "deal_node_key": TENDER, "legacy_library_id": "3020QE-td", "is_dir": "True"},
    {"node_key": TENDER + "|PO ELTA-7.pdf", "node_name": "PO ELTA-7.pdf", "libr_category": "document", "asset_class": "deal_candidate", "extension": "pdf",
     "company_node_key": ELTA, "deal_node_key": TENDER, "legacy_library_id": "3020QE-po", "is_dir": "False"},
]


def _elta_decision(tmp_path: Path, approve: str = "Y") -> Path:
    p = tmp_path / "dec_elta.csv"
    with p.open("w", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=DECISION_COLUMNS); w.writeheader()
        w.writerow({"legacy_deal_id": "3020QE-td", "deal_node_key": TENDER, "anchor_kind": "folder", "deal_name": "Tender",
                    "company_node_key": ELTA, "company_name": "ELTA", "dealname": "ELTA - Tender",
                    "pipeline": "", "dealstage": "", "amount": "", "approve": approve})
    return p


def _ledger(tmp_path: Path) -> SqliteLedger:
    l = SqliteLedger(tmp_path / "l.sqlite"); l.bootstrap()
    l.record_company({"company_node_key": CO, "company_name": "Toshiba", "hs_company_id": "1001", "status": "created"})
    for lid, nid in (("3020QT-po1", "7001"), ("3020QT-bi2", "7002"), ("3020QT-qu", "7003"), ("3020QT-solo", "7004")):
        l.record_attach({"legacy_id": lid, "hs_note_id": nid, "status": "attached"})
    return l


def _decisions(tmp_path: Path, approve: str) -> Path:
    p = tmp_path / "dec.csv"
    with p.open("w", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=DECISION_COLUMNS); w.writeheader()
        w.writerow({"legacy_deal_id": "3020QT-rfq", "deal_node_key": RFQ, "anchor_kind": "folder", "deal_name": "2026 RFQ",
                    "company_node_key": CO, "company_name": "Toshiba", "dealname": "Toshiba - 2026 RFQ",
                    "pipeline": "", "dealstage": "", "amount": "1200", "approve": approve})
        w.writerow({"legacy_deal_id": "3020QT-solo", "deal_node_key": CO + "|PO_solo.pdf", "anchor_kind": "file",
                    "deal_name": "PO_solo.pdf", "company_node_key": CO, "company_name": "Toshiba",
                    "dealname": "Toshiba - PO_solo", "pipeline": "", "dealstage": "", "amount": "", "approve": approve})
    return p


def test_not_approved_rows_are_skipped_even_live(tmp_path: Path):
    ledger = _ledger(tmp_path)
    decs = read_decisions(_decisions(tmp_path, "N"), default_pipeline="p1", default_stage="s1")
    client = FakeClient()
    out = resolve_deals(decs, hierarchy_rows=HIER, client=client, ledger=ledger, live_create=True)
    assert {r["status"] for r in out} == {"not_approved"} and client.calls == []


def test_one_deal_per_anchor_with_every_po_billing_note_associated(tmp_path: Path):
    ledger = _ledger(tmp_path)
    decs = read_decisions(_decisions(tmp_path, "Y"), default_pipeline="p1", default_stage="s1")
    client = FakeClient()

    dry = resolve_deals(decs, hierarchy_rows=HIER, client=client, ledger=ledger, live_create=False)
    assert [r["status"] for r in dry] == ["would_create", "would_create"] and client.calls == []
    assert dry[0]["notes"] == 2 and dry[0]["hs_note_id"] == "7001;7002"    # the quote (7003) is deferred, not associated
    assert dry[1]["notes"] == 1 and dry[1]["hs_note_id"] == "7004"

    live = resolve_deals(decs, hierarchy_rows=HIER, client=client, ledger=ledger, live_create=True)
    assert [r["status"] for r in live] == ["created", "created"]
    assert ("create_deal", "Toshiba - 2026 RFQ", "s1", "p1", {"amount": "1200"}) in client.calls
    assert ("assoc", "deal", "501", "company", "1001") in client.calls
    assert ("assoc", "note", "7001", "deal", "501") in client.calls and ("assoc", "note", "7002", "deal", "501") in client.calls
    assert ("assoc", "note", "7003", "deal", "501") not in client.calls
    assert ("assoc", "note", "7004", "deal", "502") in client.calls
    assert sum(1 for c in client.calls if c[0] == "create_deal") == 2

    n = len(client.calls)
    again = resolve_deals(decs, hierarchy_rows=HIER, client=client, ledger=ledger, live_create=True)
    assert {r["status"] for r in again} == {"resolved_from_ledger"} and len(client.calls) == n
    assert ledger.deal_map() == {"3020QT-rfq": "501", "3020QT-solo": "502"}


def test_missing_company_blocks_deal(tmp_path: Path):
    ledger = SqliteLedger(tmp_path / "l.sqlite"); ledger.bootstrap()
    decs = read_decisions(_decisions(tmp_path, "Y"), default_pipeline="p1", default_stage="s1")
    out = resolve_deals(decs, hierarchy_rows=HIER, client=FakeClient(), ledger=ledger, live_create=True)
    assert {r["status"] for r in out} == {"no_company_resolved"}


def test_ledger_export_writes_every_table(tmp_path: Path):
    ledger = _ledger(tmp_path)
    paths = ledger.export_tables(tmp_path / "export")
    assert set(paths) == set(LEDGER_TABLES)
    rows = list(csv.DictReader(paths["companies_resolved"].open()))
    assert rows[0]["hs_company_id"] == "1001"
    assert list(csv.DictReader(paths["deals_created"].open())) == []   # header only


# ── pass-2 orphan salvage (enrichment: search by the remainder name, never create) ──────────────

def test_salvage_matches_year_prefixed_company_in_dry_mode_records_ledger_and_associates_live(tmp_path: Path):
    ledger = _ledger(tmp_path)
    decs = read_decisions(_elta_decision(tmp_path), default_pipeline="p1", default_stage="s1")
    client = FakeClient(companies={"ELTA": [{"id": "9002", "properties": {"name": "ELTA"}}]})
    dry = resolve_deals(decs, hierarchy_rows=HIER_ELTA, client=client, ledger=ledger, live_create=False)
    assert dry[0]["status"] == "would_create" and dry[0]["hs_company_id"] == "9002"
    assert dry[0]["company_status"] == "salvaged" and dry[0]["company_name"] == "ELTA"
    assert client.calls == [("search", "ELTA")]
    assert ledger.company_rows()[ELTA]["status"] == "matched_by_name_pass2"
    assert ELTA not in ledger.company_map() and ledger.company_map(include_pass2=True)[ELTA] == "9002"   # strict pass 1
    live = resolve_deals(decs, hierarchy_rows=HIER_ELTA, client=client, ledger=ledger, live_create=True)
    assert live[0]["status"] == "created" and ("assoc", "deal", "501", "company", "9002") in client.calls
    assert client.calls.count(("search", "ELTA")) == 1          # the ledger row answers the second run
    again = resolve_deals(decs, hierarchy_rows=HIER_ELTA, client=client, ledger=ledger, live_create=True)
    assert again[0]["status"] == "resolved_from_ledger"


def test_salvage_not_found_blocks_the_deal_names_the_companion_file_and_persists_the_miss(tmp_path: Path):
    ledger = _ledger(tmp_path)
    decs = read_decisions(_elta_decision(tmp_path), default_pipeline="p1", default_stage="s1")
    client = FakeClient()
    out = resolve_deals(decs, hierarchy_rows=HIER_ELTA, client=client, ledger=ledger, live_create=True)
    assert out[0]["status"] == "no_company_resolved" and out[0]["company_status"] == "missing_in_portal"
    assert "hubspot_companies_import.csv" in out[0]["error"]
    assert ledger.company_rows()[ELTA]["status"] == "not_in_portal_pass2" and ledger.company_rows()[ELTA]["hs_company_id"] is None
    assert not any(c[0] == "create_deal" for c in client.calls)
    # a company imported meanwhile is found on the next run (the miss is not cached as final)
    client.companies["ELTA"] = [{"id": "9009", "properties": {"name": "ELTA"}}]
    out2 = resolve_deals(decs, hierarchy_rows=HIER_ELTA, client=client, ledger=ledger, live_create=False)
    assert out2[0]["status"] == "would_create" and out2[0]["hs_company_id"] == "9009"


def test_salvage_ambiguous_blocks_the_deal(tmp_path: Path):
    ledger = _ledger(tmp_path)
    decs = read_decisions(_elta_decision(tmp_path), default_pipeline="p1", default_stage="s1")
    client = FakeClient(companies={"ELTA": [{"id": "1"}, {"id": "2"}]})
    out = resolve_deals(decs, hierarchy_rows=HIER_ELTA, client=client, ledger=ledger, live_create=True)
    assert out[0]["status"] == "no_company_resolved" and out[0]["company_status"] == "ambiguous"
    assert ledger.company_rows()[ELTA]["status"] == "ambiguous_match_pass2"


def test_salvage_never_touches_company_folder_keys_and_needs_a_token(tmp_path: Path):
    ledger = SqliteLedger(tmp_path / "l.sqlite"); ledger.bootstrap()          # Toshiba (company_folder) NOT resolved
    decs = read_decisions(_decisions(tmp_path, "Y"), default_pipeline="p1", default_stage="s1")
    client = FakeClient(companies={"Toshiba": [{"id": "1001"}]})
    out = resolve_deals(decs, hierarchy_rows=HIER, client=client, ledger=ledger, live_create=True)
    assert {r["status"] for r in out} == {"no_company_resolved"} and client.calls == []      # companies.py territory
    assert all(r["error"] == "company not in ledger; run `companies` live first" for r in out)
    # no token: the salvage cannot search, records nothing, says so
    decs2 = read_decisions(_elta_decision(tmp_path), default_pipeline="p1", default_stage="s1")
    out2 = resolve_deals(decs2, hierarchy_rows=HIER_ELTA, client=None, ledger=ledger, live_create=False)
    assert out2[0]["status"] == "no_company_resolved" and "no HubSpot token" in out2[0]["error"]
    assert ELTA not in ledger.company_rows()


def test_salvage_searches_once_per_company_even_for_unapproved_rows(tmp_path: Path):
    ledger = _ledger(tmp_path)
    p = tmp_path / "dec2.csv"
    with p.open("w", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=DECISION_COLUMNS); w.writeheader()
        for i, appr in ((1, "Y"), (2, "N")):
            w.writerow({"legacy_deal_id": f"3020QE-t{i}", "deal_node_key": f"{ELTA}|T{i}", "anchor_kind": "folder",
                        "deal_name": f"T{i}", "company_node_key": ELTA, "company_name": "ELTA", "dealname": f"ELTA - T{i}",
                        "pipeline": "", "dealstage": "", "amount": "", "approve": appr})
    decs = read_decisions(p, default_pipeline="p1", default_stage="s1")
    client = FakeClient(companies={"ELTA": [{"id": "9002"}]})
    out = resolve_deals(decs, hierarchy_rows=HIER_ELTA, client=client, ledger=ledger, live_create=False)
    assert [r["status"] for r in out] == ["would_create", "not_approved"] and client.calls == [("search", "ELTA")]
    assert all(r["company_status"] == "salvaged" for r in out)      # the companion file needs the status for every anchor
