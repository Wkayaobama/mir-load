"""Pass 2 — inferred deals → HubSpot deals, from an operator-approved decisions file.

Never runs inside the DFS. The deal ANCHOR is what the walker + card inferred (see
deal_anchors.py): a qualified level-3 folder, or a PO/Billing PDF sitting directly under the
company. ``runner review-export`` writes one decisions row per anchor; the operator edits
approve=Y, dealname, pipeline, dealstage, amount. For each approved row, behind the
MRLOAD_APPROVE_DEAL_CREATE gate:

  1. POST /crm/v3/objects/deals                  (dealname, dealstage, pipeline, amount)
  2. PUT v4 default association deal → company   (the anchor's company)
  3. PUT v4 default association note → deal      for every PO/Billing PDF beneath the anchor
                                                 whose pass-1 note exists (card: pass_2_associates)
     Other documents beneath the anchor carry legacy_deal_id but are deferred (notes API later).

Idempotent through ledger.deals_created keyed by legacy_deal_id (stored in legacy_library_id:
it IS the anchor node's library id). hs_note_id holds the associated note ids, ';'-joined.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from .card import LibraryCard
from .client import HubSpotClient
from .deal_anchors import DealAnchor, qualify_deal_anchors
from .ledger import LedgerLike

DECISION_COLUMNS = [
    "legacy_deal_id", "deal_node_key", "anchor_kind", "deal_name", "company_node_key", "company_name",
    "pdf_count", "deal_candidate_count", "asset_count", "link",
    "dealname", "pipeline", "dealstage", "amount", "approve", "operator_note",
]

STATUS_LEDGER = "resolved_from_ledger"
STATUS_CREATED = "created"
STATUS_WOULD_CREATE = "would_create"
STATUS_SKIPPED = "not_approved"
STATUS_NO_COMPANY = "no_company_resolved"
STATUS_DRY_NO_TOKEN = "dry_no_token"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"


@dataclass
class DealDecision:
    legacy_deal_id: str
    deal_node_key: str
    anchor_kind: str
    company_node_key: str
    dealname: str
    dealstage: str
    pipeline: Optional[str] = None
    amount: Optional[str] = None
    approve: bool = False
    deal_name: str = ""


def _truthy(v: object) -> bool:
    return str(v or "").strip().lower() in ("y", "yes", "true", "1")


def read_decisions(path: Path, *, default_pipeline: Optional[str], default_stage: Optional[str]) -> list[DealDecision]:
    out: list[DealDecision] = []
    with path.open("r", encoding="utf-8-sig", newline="") as fp:
        for r in csv.DictReader(fp):
            out.append(
                DealDecision(
                    legacy_deal_id=(r.get("legacy_deal_id") or "").strip(),
                    deal_node_key=(r.get("deal_node_key") or "").strip(),
                    anchor_kind=(r.get("anchor_kind") or "folder").strip(),
                    company_node_key=(r.get("company_node_key") or "").strip(),
                    dealname=(r.get("dealname") or "").strip(),
                    dealstage=(r.get("dealstage") or default_stage or "").strip(),
                    pipeline=(r.get("pipeline") or default_pipeline or "").strip() or None,
                    amount=(r.get("amount") or "").strip() or None,
                    approve=_truthy(r.get("approve")),
                    deal_name=(r.get("deal_name") or "").strip(),
                )
            )
    return out


def write_decisions_template(rows: Iterable[dict], out_path: Path, *, card: LibraryCard) -> int:
    """One decisions row per QUALIFIED anchor (approve=N by default): folders first, then self-anchored PDFs."""
    rows = list(rows)
    company_names = {r["node_key"]: r["node_name"] for r in rows if r.get("libr_category") in ("company_folder", "engagement_folder")}
    anchors = qualify_deal_anchors(rows, card)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out_path.open("w", encoding="utf-8", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=DECISION_COLUMNS)
        w.writeheader()
        for a in sorted(anchors.values(), key=lambda a: (a.anchor_kind != "folder", a.company_node_key, a.deal_name)):
            company = company_names.get(a.company_node_key, "")
            stem = a.deal_name.rsplit(".", 1)[0] if a.anchor_kind == "file" else a.deal_name
            w.writerow({
                "legacy_deal_id": a.legacy_deal_id, "deal_node_key": a.deal_node_key, "anchor_kind": a.anchor_kind,
                "deal_name": a.deal_name, "company_node_key": a.company_node_key, "company_name": company,
                "pdf_count": a.pdf_count, "deal_candidate_count": a.deal_candidate_count, "asset_count": a.asset_count,
                "link": a.link, "dealname": f"{company} - {stem}" if company else stem,
                "pipeline": "", "dealstage": "", "amount": "", "approve": "N", "operator_note": "",
            })
            n += 1
    return n


def notes_to_associate(decision: DealDecision, hierarchy_rows: Iterable[dict], note_map: dict[str, str],
                       *, classes: Iterable[str] = ("deal_candidate",)) -> list[str]:
    """hs_note_ids of the pass-1 notes that belong to this deal: the PO/Billing PDFs beneath the anchor
    (folder anchor) or the anchor file itself (file anchor). Missing notes are simply absent."""
    classes = set(classes)
    ids: list[str] = []
    for r in hierarchy_rows:
        if str(r.get("is_dir")) not in ("False", "false", "0", ""):
            continue
        if r.get("asset_class") not in classes:
            continue
        owns = (r.get("deal_node_key") == decision.deal_node_key) if decision.anchor_kind == "folder" \
            else (r.get("node_key") == decision.deal_node_key)
        if owns and note_map.get(r["legacy_library_id"]):
            ids.append(note_map[r["legacy_library_id"]])
    return ids


def resolve_deals(
    decisions: Iterable[DealDecision],
    *,
    hierarchy_rows: Iterable[dict],
    client: Optional[HubSpotClient],
    ledger: LedgerLike,
    live_create: bool,
    associate_classes: Iterable[str] = ("deal_candidate",),
) -> list[dict]:
    hierarchy_rows = list(hierarchy_rows)
    company_map = ledger.company_map()
    note_map = ledger.note_map()
    known = ledger.deal_map()
    results: list[dict] = []
    for d in decisions:
        note_ids = notes_to_associate(d, hierarchy_rows, note_map, classes=associate_classes)
        entry = {
            "legacy_library_id": d.legacy_deal_id, "dealname": d.dealname, "anchor_kind": d.anchor_kind,
            "hs_deal_id": None, "hs_company_id": company_map.get(d.company_node_key),
            "hs_note_id": ";".join(note_ids) or None, "notes": len(note_ids), "status": None, "error": None,
        }
        if d.legacy_deal_id in known:
            entry.update(hs_deal_id=known[d.legacy_deal_id], status=STATUS_LEDGER)
            results.append(entry)
            continue
        if not d.approve:
            entry["status"] = STATUS_SKIPPED
            results.append(entry)
            continue
        if not entry["hs_company_id"]:
            entry.update(status=STATUS_NO_COMPANY, error="company not in ledger; run `companies` live first")
            results.append(entry)
            continue
        if not d.dealname or not d.dealstage:
            entry.update(status=STATUS_FAILED, error="dealname and dealstage are required")
            results.append(entry)
            continue
        if client is None:
            entry["status"] = STATUS_DRY_NO_TOKEN
            results.append(entry)
            continue
        if not live_create:
            entry["status"] = STATUS_WOULD_CREATE
            results.append(entry)
            continue
        try:
            extra = {"amount": d.amount} if d.amount else {}
            created = client.create_deal(dealname=d.dealname, dealstage=d.dealstage, pipeline=d.pipeline, **extra)
            entry["hs_deal_id"] = str(created["id"])
        except Exception as exc:
            entry.update(status=STATUS_FAILED, error=f"create_error: {exc}")
            results.append(entry)
            ledger.record_deal(entry)
            continue
        failures: list[str] = []
        try:
            client.associate_default("deal", entry["hs_deal_id"], "company", entry["hs_company_id"])
        except Exception as exc:
            failures.append(f"deal→company ({exc})")
        for nid in note_ids:
            try:
                client.associate_default("note", nid, "deal", entry["hs_deal_id"])
            except Exception as exc:
                failures.append(f"note {nid}→deal ({exc})")
        entry["status"] = STATUS_PARTIAL if failures else STATUS_CREATED
        entry["error"] = "; ".join(failures) or None
        results.append(entry)
        ledger.record_deal(entry)
    return results
