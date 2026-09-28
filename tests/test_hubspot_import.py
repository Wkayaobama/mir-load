"""HubSpot-Import-ready CSVs: card-derived columns, ids from ledger/results, companion companies file."""
from __future__ import annotations

import csv
from pathlib import Path

from pipeline.library_files.card import load_library_card
from pipeline.library_files.deal_anchors import qualify_deal_anchors
from pipeline.library_files.deals import read_decisions
from pipeline.library_files.hubspot_import import (DEAL_HEAD, DEAL_TAIL, company_import_columns, deal_import_columns,
                                                   write_hubspot_import_files)
from pipeline.library_files.ledger import SqliteLedger
from tests.test_deals import CO, DECISION_COLUMNS, ELTA, HIER_ELTA, RFQ, TENDER, _elta_decision


def _read(p: Path) -> list[dict]:
    return list(csv.DictReader(p.open(encoding="utf-8", newline="")))


def test_deal_import_columns_are_head_plus_card_deals_fields_plus_bookkeeping():
    card = load_library_card()
    cols = deal_import_columns(card)
    assert cols[:8] == DEAL_HEAD and cols[-4:] == DEAL_TAIL
    assert cols[8:-4] == ["mrload_legacy_deal_id", "mrload_deal_node_key", "mrload_anchor_kind", "mrload_legacy_company_id",
                          "mrload_segment", "mrload_drive_folder_id", "mrload_drive_link", "mrload_pdf_count",
                          "mrload_deal_candidate_count", "mrload_asset_count", "mrload_drive_modified_at"]
    assert company_import_columns(card)[:3] == ["Company name", "Company Domain Name", "Description"]


def test_deals_import_one_row_per_anchor_with_ids_from_ledger_and_blank_pipeline(tmp_path: Path):
    card = load_library_card()
    anchors = qualify_deal_anchors(HIER_ELTA, card)
    ledger = SqliteLedger(tmp_path / "l.sqlite"); ledger.bootstrap()
    ledger.record_company({"company_node_key": CO, "company_name": "Toshiba", "hs_company_id": "1001", "status": "created"})
    ledger.record_deal({"legacy_library_id": "3020QT-rfq", "hs_deal_id": "501", "dealname": "Toshiba - 2026 RFQ",
                        "hs_company_id": "1001", "hs_note_id": "7001;7002", "status": "created", "error": None})
    ledger.record_company({"company_node_key": ELTA, "company_name": "ELTA", "hs_company_id": None, "status": "not_in_portal_pass2"})
    summary = write_hubspot_import_files(HIER_ELTA, anchors, out_dir=tmp_path, card=card, ledger=ledger)
    deals = {r["mrload_legacy_deal_id"]: r for r in _read(tmp_path / "hubspot_deals_import.csv")}
    assert set(deals) == {"3020QT-rfq", "3020QT-solo", "3020QE-td"} and summary["hubspot_deals_import.csv"] == 3
    rfq = deals["3020QT-rfq"]
    assert rfq["Record ID"] == "501" and rfq["Company Record ID"] == "1001" and rfq["Company Name"] == "Toshiba"
    assert rfq["Pipeline"] == "" and rfq["Deal Stage"] == "" and rfq["API status (mr-load)"] == "created"
    assert rfq["mrload_legacy_company_id"] == "3020Q-co" and rfq["mrload_deal_node_key"] == RFQ and rfq["Approve (mr-load)"] == "N"
    solo = deals["3020QT-solo"]
    assert solo["Record ID"] == "" and solo["API status (mr-load)"] == "not_created" and solo["Deal Name"] == "Toshiba - PO_solo"
    tender = deals["3020QE-td"]
    assert tender["Company Name"] == "ELTA" and tender["Company Record ID"] == "" and tender["Company status (mr-load)"] == "missing_in_portal"
    comp = _read(tmp_path / "hubspot_companies_import.csv")
    assert [c["Company name"] for c in comp] == ["ELTA"] and comp[0]["mrload_company_node_key"] == ELTA
    assert comp[0]["mrload_asset_count"] == "1" and comp[0]["mrload_deal_candidate_count"] == "1" and comp[0]["Company Domain Name"] == ""
    assert summary["hubspot_companies_import.csv"] == 1 and summary["unmapped_card_columns"] == []


def test_deals_import_without_ledger_or_decisions_uses_template_defaults(tmp_path: Path):
    card = load_library_card()
    anchors = qualify_deal_anchors(HIER_ELTA, card)
    summary = write_hubspot_import_files(HIER_ELTA, anchors, out_dir=tmp_path, card=card)
    deals = _read(tmp_path / "hubspot_deals_import.csv")
    assert all(r["Record ID"] == "" and r["Company Record ID"] == "" and r["Approve (mr-load)"] == "N" for r in deals)
    assert {r["Company status (mr-load)"] for r in deals} == {"unresolved"}
    assert next(r for r in deals if r["mrload_legacy_deal_id"] == "3020QT-rfq")["Deal Name"] == "Toshiba - 2026 RFQ"
    assert _read(tmp_path / "hubspot_companies_import.csv") == [] and summary["hubspot_companies_import.csv"] == 0


def test_results_overlay_beats_ledger_and_approved_rows_come_first(tmp_path: Path):
    card = load_library_card()
    anchors = qualify_deal_anchors(HIER_ELTA, card)
    decisions = read_decisions(_elta_decision(tmp_path, "Y"), default_pipeline=None, default_stage=None)
    results = [{"legacy_library_id": "3020QE-td", "hs_deal_id": None, "hs_company_id": "9002", "company_status": "salvaged",
                "status": "would_create", "error": None}]
    write_hubspot_import_files(HIER_ELTA, anchors, out_dir=tmp_path, card=card, decisions=decisions, results=results)
    deals = _read(tmp_path / "hubspot_deals_import.csv")
    assert deals[0]["mrload_legacy_deal_id"] == "3020QE-td" and deals[0]["Approve (mr-load)"] == "Y"   # approved first
    assert deals[0]["Company Record ID"] == "9002" and deals[0]["Company status (mr-load)"] == "salvaged"
    assert deals[0]["API status (mr-load)"] == "would_create" and deals[0]["Deal Name"] == "ELTA - Tender"


def test_unknown_card_column_is_blank_and_reported(tmp_path: Path):
    card = load_library_card()
    card.raw["hubspot"]["properties"]["objects"]["deals"]["fields"].append(
        {"name": "mrload_nonexistent", "column": "nonexistent", "type": "string"})
    anchors = qualify_deal_anchors(HIER_ELTA, card)
    summary = write_hubspot_import_files(HIER_ELTA, anchors, out_dir=tmp_path, card=card)
    deals = _read(tmp_path / "hubspot_deals_import.csv")
    assert all(r["mrload_nonexistent"] == "" for r in deals) and summary["unmapped_card_columns"] == ["nonexistent"]
