"""Pass 2: decisions template, approval gating, deal create + associations,
idempotent re-run, and the ledger CSV export (step 6)."""
from __future__ import annotations

import csv
from pathlib import Path

from pipeline.library_files.deals import (
    DECISION_COLUMNS, read_decisions, resolve_deals, write_decisions_template,
)
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


HIER = [
    {"node_key": "K|Quantum|Toshiba", "node_name": "Toshiba", "libr_category": "company_folder",
     "asset_class": "", "company_node_key": "", "legacy_library_id": "3020Q-co", "link": "", "is_dir": "True"},
    {"node_key": "K|Quantum|Toshiba|PO_4711.pdf", "node_name": "PO_4711.pdf", "libr_category": "document",
     "asset_class": "deal_candidate", "company_node_key": "K|Quantum|Toshiba", "legacy_library_id": "3020QT-po",
     "link": "https://drive/x", "is_dir": "False"},
    {"node_key": "K|Quantum|Toshiba|spec.pdf", "node_name": "spec.pdf", "libr_category": "document",
     "asset_class": "parked_for_review", "company_node_key": "K|Quantum|Toshiba", "legacy_library_id": "3020QT-sp",
     "link": "", "is_dir": "False"},
]


def test_decisions_template_lists_only_deal_candidates(tmp_path: Path):
    out = tmp_path / "deal_decisions.csv"
    assert write_decisions_template(HIER, out) == 1
    rows = list(csv.DictReader(out.open()))
    assert list(rows[0].keys()) == DECISION_COLUMNS
    assert rows[0]["dealname"] == "Toshiba - PO_4711" and rows[0]["approve"] == "N"


def _ledger(tmp_path: Path) -> SqliteLedger:
    l = SqliteLedger(tmp_path / "l.sqlite"); l.bootstrap()
    l.record_company({"company_node_key": "K|Quantum|Toshiba", "company_name": "Toshiba",
                      "hs_company_id": "1001", "status": "created"})
    l.record_attach({"legacy_id": "3020QT-po", "hs_note_id": "7007", "status": "attached"})
    return l


def _decisions(tmp_path: Path, approve: str) -> Path:
    p = tmp_path / "dec.csv"
    with p.open("w", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=DECISION_COLUMNS); w.writeheader()
        w.writerow({"legacy_library_id": "3020QT-po", "company_node_key": "K|Quantum|Toshiba",
                    "company_name": "Toshiba", "file_name": "PO_4711.pdf", "dealname": "Toshiba - PO 4711",
                    "pipeline": "", "dealstage": "", "amount": "1200", "approve": approve})
    return p


def test_not_approved_rows_are_skipped_even_live(tmp_path: Path):
    ledger = _ledger(tmp_path)
    decs = read_decisions(_decisions(tmp_path, "N"), default_pipeline="p1", default_stage="s1")
    client = FakeClient()
    out = resolve_deals(decs, client=client, ledger=ledger, live_create=True)
    assert out[0]["status"] == "not_approved" and client.calls == []


def test_dry_then_live_then_idempotent(tmp_path: Path):
    ledger = _ledger(tmp_path)
    decs = read_decisions(_decisions(tmp_path, "Y"), default_pipeline="p1", default_stage="s1")
    client = FakeClient()

    dry = resolve_deals(decs, client=client, ledger=ledger, live_create=False)
    assert dry[0]["status"] == "would_create" and client.calls == []

    live = resolve_deals(decs, client=client, ledger=ledger, live_create=True)
    assert live[0]["status"] == "created" and live[0]["hs_deal_id"] == "501"
    assert ("create_deal", "Toshiba - PO 4711", "s1", "p1", {"amount": "1200"}) in client.calls
    assert ("assoc", "deal", "501", "company", "1001") in client.calls
    assert ("assoc", "note", "7007", "deal", "501") in client.calls

    n = len(client.calls)
    again = resolve_deals(decs, client=client, ledger=ledger, live_create=True)
    assert again[0]["status"] == "resolved_from_ledger" and len(client.calls) == n


def test_missing_company_blocks_deal(tmp_path: Path):
    ledger = SqliteLedger(tmp_path / "l.sqlite"); ledger.bootstrap()
    decs = read_decisions(_decisions(tmp_path, "Y"), default_pipeline="p1", default_stage="s1")
    out = resolve_deals(decs, client=FakeClient(), ledger=ledger, live_create=True)
    assert out[0]["status"] == "no_company_resolved"


def test_ledger_export_writes_every_table(tmp_path: Path):
    ledger = _ledger(tmp_path)
    paths = ledger.export_tables(tmp_path / "export")
    assert set(paths) == set(LEDGER_TABLES)
    rows = list(csv.DictReader(paths["companies_resolved"].open()))
    assert rows[0]["hs_company_id"] == "1001"
    assert list(csv.DictReader(paths["deals_created"].open())) == []   # header only
