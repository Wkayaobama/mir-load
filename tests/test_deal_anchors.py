"""Deal layer: structural key from the walker (level 3, inherited) + heuristic qualification (PDF
beneath, name outside the exclusion list, PO/Billing PDFs directly under the company self-anchor)."""
from __future__ import annotations

import csv
from pathlib import Path

from pipeline.library_files.card import load_library_card
from pipeline.library_files.deal_anchors import deal_anchor_key_for_row, deal_documents, qualify_deal_anchors
from pipeline.library_files.deals import DECISION_COLUMNS, write_decisions_template
from pipeline.library_files.hierarchy import HierarchyWriter, read_hierarchy_csv
from pipeline.library_files.manifest import ManifestEntry
from pipeline.library_files.walker import DriveTreeWalker

FOLDER = "application/vnd.google-apps.folder"


def _e(path, *, is_dir=False, mime="application/pdf"):
    return ManifestEntry(path=path, name=path.rsplit("/", 1)[-1], is_dir=is_dir,
                         drive_id="d" + str(abs(hash(path)) % 10**6), mime_type=FOLDER if is_dir else mime,
                         size=None if is_dir else 10, mod_time="2026-06-01T00:00:00.000Z", md5=None)


ENTRIES = [
    _e("Quantum", is_dir=True),
    _e("Quantum/Toshiba", is_dir=True),
    _e("Quantum/Toshiba/Toshiba PO 2026-001.pdf"),                       # directly under the company → self-anchored deal
    _e("Quantum/Toshiba/spec sheet.pdf"),                                # directly under the company → no deal
    _e("Quantum/Toshiba/2026 Quantum sensor RFQ", is_dir=True),          # level 3, PDFs beneath → deal
    _e("Quantum/Toshiba/2026 Quantum sensor RFQ/Quote QS-17.pdf"),
    _e("Quantum/Toshiba/2026 Quantum sensor RFQ/PO 2026-042.pdf"),
    _e("Quantum/Toshiba/2026 Quantum sensor RFQ/SOW.docx", mime="application/msword"),
    _e("Quantum/Toshiba/2026 Quantum sensor RFQ/drawings", is_dir=True),  # level 4 inherits
    _e("Quantum/Toshiba/2026 Quantum sensor RFQ/drawings/layout.gds", mime="application/octet-stream"),
    _e("Quantum/Toshiba/Site survey", is_dir=True),                      # level 3, no PDF → not a deal
    _e("Quantum/Toshiba/Site survey/photos.docx", mime="application/msword"),
    _e("Quantum/Toshiba/2025 Photonics West Exhibition", is_dir=True),   # excluded realm
    _e("Quantum/Toshiba/2025 Photonics West Exhibition/booth quote.pdf"),
    _e("Quantum/2021_ELTA", is_dir=True),                                # year-prefixed at company level (untouched behaviour)
    _e("Quantum/2021_ELTA/Tender", is_dir=True),
    _e("Quantum/2021_ELTA/Tender/offer.pdf"),
]


def _rows(tmp_path: Path) -> list[dict]:
    card = load_library_card()
    w = HierarchyWriter(DriveTreeWalker(path_prefix="30 Sales", root_name="20 opportunities and customer data",
                                        segment_depth=1, card=card))
    out = tmp_path / "h.csv"
    w.write_csv(ENTRIES, out)
    return read_hierarchy_csv(out)


def test_walker_sets_deal_node_key_only_from_level_3_and_inherits_it(tmp_path):
    by = {r["rel_path"]: r for r in _rows(tmp_path)}
    rfq = by["Quantum/Toshiba/2026 Quantum sensor RFQ"]
    assert rfq["deal_node_key"] == rfq["node_key"] and rfq["libr_category"] == "engagement_folder"   # year prefix: grammar rule 1, untouched
    assert by["Quantum/Toshiba/2026 Quantum sensor RFQ/drawings/layout.gds"]["deal_node_key"] == rfq["node_key"]
    assert by["Quantum/Toshiba/2026 Quantum sensor RFQ/drawings"]["deal_node_key"] == rfq["node_key"]
    assert by["Quantum/Toshiba/Toshiba PO 2026-001.pdf"]["deal_node_key"] == ""      # directly under the company
    assert by["Quantum/Toshiba"]["deal_node_key"] == "" and by["Quantum"]["deal_node_key"] == ""
    # the untouched year-prefixed company-level folder still anchors its own level 3
    tender = by["Quantum/2021_ELTA/Tender"]
    assert tender["deal_node_key"] == tender["node_key"] and by["Quantum/2021_ELTA/Tender/offer.pdf"]["deal_node_key"] == tender["node_key"]
    # company keys untouched by the deal layer
    assert by["Quantum/Toshiba/2026 Quantum sensor RFQ/PO 2026-042.pdf"]["company_node_key"] == by["Quantum/Toshiba"]["node_key"]


def test_qualification_pdf_beneath_exclusion_and_self_anchor(tmp_path):
    rows = _rows(tmp_path)
    card = load_library_card()
    anchors = qualify_deal_anchors(rows, card)
    names = {a.deal_name: a for a in anchors.values()}
    assert set(names) == {"2026 Quantum sensor RFQ", "Toshiba PO 2026-001.pdf", "Tender"}
    rfq = names["2026 Quantum sensor RFQ"]
    assert rfq.anchor_kind == "folder" and rfq.pdf_count == 2 and rfq.deal_candidate_count == 1 and rfq.asset_count == 4
    assert names["Toshiba PO 2026-001.pdf"].anchor_kind == "file"
    assert "Site survey" not in names and "2025 Photonics West Exhibition" not in names
    by = {r["rel_path"]: r for r in rows}
    assert deal_anchor_key_for_row(by["Quantum/Toshiba/2026 Quantum sensor RFQ/SOW.docx"], anchors) == rfq.deal_node_key
    assert deal_anchor_key_for_row(by["Quantum/Toshiba/spec sheet.pdf"], anchors) is None
    assert deal_anchor_key_for_row(by["Quantum/Toshiba/2025 Photonics West Exhibition/booth quote.pdf"], anchors) is None
    deferred = deal_documents(rows, anchors)
    assert {d["node_name"] for d in deferred} == {"Quote QS-17.pdf", "SOW.docx", "layout.gds", "offer.pdf"}
    assert all(d["legacy_deal_id"] for d in deferred)


def test_min_pdf_and_exclusion_are_card_driven(tmp_path):
    rows = _rows(tmp_path)
    card = load_library_card()
    card.deal_min_pdf = 3
    assert "2026 Quantum sensor RFQ" not in {a.deal_name for a in qualify_deal_anchors(rows, card).values()}
    card.deal_min_pdf = 1
    card.deal_exclude_patterns = []
    assert "2025 Photonics West Exhibition" in {a.deal_name for a in qualify_deal_anchors(rows, card).values()}


def test_decisions_template_one_row_per_anchor(tmp_path):
    rows = _rows(tmp_path)
    out = tmp_path / "deal_decisions.csv"
    assert write_decisions_template(rows, out, card=load_library_card()) == 3
    got = list(csv.DictReader(out.open(encoding="utf-8")))
    assert list(got[0].keys()) == DECISION_COLUMNS
    assert [g["anchor_kind"] for g in got] == ["folder", "folder", "file"]    # folders first
    rfq = next(g for g in got if g["deal_name"] == "2026 Quantum sensor RFQ")
    assert rfq["dealname"] == "Toshiba - 2026 Quantum sensor RFQ" and rfq["approve"] == "N" and rfq["pdf_count"] == "2"
    po = next(g for g in got if g["anchor_kind"] == "file")
    assert po["dealname"] == "Toshiba - Toshiba PO 2026-001"
