"""Pass 2 on the inferred anchor: approval gating, one deal per anchor, every PO/Billing note
beneath it associated, deferred documents untouched, idempotent re-run, ledger export."""
from __future__ import annotations

import csv
from pathlib import Path

from pipeline.library_files.deals import DECISION_COLUMNS, read_decisions, resolve_deals
from pipeline.library_files.ledger import LEDGER_TABLES, SqliteLedger


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self._n = 500

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
    {"node_key": RFQ + "|PO_1.pdf", "node_name": "PO_1.pdf", "libr_category": "document", "asset_class": "deal_candidate",
     "company_node_key": CO, "deal_node_key": RFQ, "legacy_library_id": "3020QT-po1", "is_dir": "False"},
    {"node_key": RFQ + "|Billing_2.pdf", "node_name": "Billing_2.pdf", "libr_category": "document", "asset_class": "deal_candidate",
     "company_node_key": CO, "deal_node_key": RFQ, "legacy_library_id": "3020QT-bi2", "is_dir": "False"},
    {"node_key": RFQ + "|quote.pdf", "node_name": "quote.pdf", "libr_category": "document", "asset_class": "parked_for_review",
     "company_node_key": CO, "deal_node_key": RFQ, "legacy_library_id": "3020QT-qu", "is_dir": "False"},
    {"node_key": CO + "|PO_solo.pdf", "node_name": "PO_solo.pdf", "libr_category": "document", "asset_class": "deal_candidate",
     "company_node_key": CO, "deal_node_key": "", "legacy_library_id": "3020QT-solo", "is_dir": "False"},
]


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
