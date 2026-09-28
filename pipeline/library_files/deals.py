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

Orphan salvage (``salvage_companies``): an anchor whose company is not in the ledger because its
company node is a self-anchored year-prefixed folder ("2021_ELTA" → not a company_folder row, never
resolved by companies.py) is looked up in HubSpot by the remainder name; found → associated; not found
→ listed in hubspot_companies_import.csv. Pass 2 never creates companies. Every run also refreshes the
HubSpot-Import-ready files (hubspot_import.py) so a deal load is possible without the API path.

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
from .deal_anchors import DealAnchor, company_display_name, qualify_deal_anchors
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

# companies_resolved statuses written by the pass-2 salvage (never by companies.py); the ledger's
# default company_map() hides them so pass 1 (attach) keeps its exact scope.
CO_STATUS_SALVAGED = "matched_by_name_pass2"
CO_STATUS_NOT_IN_PORTAL = "not_in_portal_pass2"
CO_STATUS_AMBIGUOUS = "ambiguous_match_pass2"
DEALS_IMPORT_FILE = "hubspot_deals_import.csv"
COMPANIES_IMPORT_FILE = "hubspot_companies_import.csv"
PASS1_COMPANY_ERROR = "company not in ledger; run `companies` live first"


def default_dealname(company_name: str, anchor: DealAnchor) -> str:
    """'<company> - <anchor>' (extension stripped for file anchors) — shared by the template and the import file."""
    stem = anchor.deal_name.rsplit(".", 1)[0] if anchor.anchor_kind == "file" else anchor.deal_name
    return f"{company_name} - {stem}" if company_name else stem


def company_status_from_ledger(row: Optional[dict]) -> str:
    """resolved (pass 1) | salvaged (pass 2 search) | missing_in_portal | ambiguous | unresolved."""
    if not row:
        return "unresolved"
    st = row.get("status") or ""
    if row.get("hs_company_id"):
        return "salvaged" if st == CO_STATUS_SALVAGED else "resolved"
    if st == CO_STATUS_NOT_IN_PORTAL:
        return "missing_in_portal"
    if st in (CO_STATUS_AMBIGUOUS, "ambiguous_match"):
        return "ambiguous"
    return "unresolved"


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
    by_key = {r["node_key"]: r for r in rows}
    anchors = qualify_deal_anchors(rows, card)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out_path.open("w", encoding="utf-8", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=DECISION_COLUMNS)
        w.writeheader()
        for a in sorted(anchors.values(), key=lambda a: (a.anchor_kind != "folder", a.company_node_key, a.deal_name)):
            company = company_display_name(by_key.get(a.company_node_key))   # "ELTA" for "2021_ELTA"
            w.writerow({
                "legacy_deal_id": a.legacy_deal_id, "deal_node_key": a.deal_node_key, "anchor_kind": a.anchor_kind,
                "deal_name": a.deal_name, "company_node_key": a.company_node_key, "company_name": company,
                "pdf_count": a.pdf_count, "deal_candidate_count": a.deal_candidate_count, "asset_count": a.asset_count,
                "link": a.link, "dealname": default_dealname(company, a),
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


def salvage_companies(
    decisions: Iterable[DealDecision],
    *,
    hierarchy_rows: list[dict],
    client: Optional[HubSpotClient],
    ledger: LedgerLike,
    company_map: dict[str, str],
) -> dict[str, dict]:
    """Pass-2 enrichment — orphan deals get a company to associate with; NEVER creates one.

    Once per distinct company_node_key referenced by any decision row that is missing from
    ``company_map`` and whose hierarchy row is NOT a company_folder (those belong to companies.py and
    keep their exact pass-1 behaviour): search HubSpot by the display name (the remainder for a
    year-prefixed company-level folder, e.g. "ELTA"). One hit → ``company_map`` gains it and the ledger
    gets a ``matched_by_name_pass2`` row; none → ``not_in_portal_pass2`` (null id, so the next run
    searches again and finds a company the operator imported meanwhile); several → ``ambiguous_match_pass2``.
    No client or a transport error → nothing recorded. Runs in dry mode too: like companies-dry, the
    ledger records what HubSpot IS; only creates and associations sit behind the gate."""
    by_key = {r["node_key"]: r for r in hierarchy_rows}
    out: dict[str, dict] = {}
    for key in dict.fromkeys(d.company_node_key for d in decisions if d.company_node_key):
        if key in company_map:
            continue
        row = by_key.get(key)
        if row is None or row.get("libr_category") == "company_folder":
            continue                                   # companies.py territory: untouched
        name = company_display_name(row)
        info = {"company_node_key": key, "company_name": name, "hs_company_id": None,
                "company_status": "unresolved", "error": None}
        if not name:
            info["error"] = "company node has no name"
            out[key] = info
            continue
        if client is None:
            info["error"] = f"company {name!r} not in ledger and no HubSpot token to search it (pass-2 salvage)"
            out[key] = info
            continue
        try:
            hits = client.search_companies_by_name(name)
        except Exception as exc:
            info.update(company_status="search_error", error=f"search_error: {exc}")
            out[key] = info
            continue
        if len(hits) == 1:
            hs_id = str(hits[0]["id"])
            company_map[key] = hs_id
            info.update(hs_company_id=hs_id, company_status="salvaged")
            ledger.record_company({"company_node_key": key, "company_name": name, "hs_company_id": hs_id,
                                   "status": CO_STATUS_SALVAGED, "error": None})
        elif not hits:
            info.update(company_status="missing_in_portal",
                        error=f"company {name!r} not in portal — import {COMPANIES_IMPORT_FILE}, then re-run deals-dry")
            ledger.record_company({"company_node_key": key, "company_name": name, "hs_company_id": None,
                                   "status": CO_STATUS_NOT_IN_PORTAL, "error": info["error"]})
        else:
            info.update(company_status="ambiguous",
                        error=f"{len(hits)} companies named {name!r} in the portal — merge/rename, then re-run deals-dry")
            ledger.record_company({"company_node_key": key, "company_name": name, "hs_company_id": None,
                                   "status": CO_STATUS_AMBIGUOUS, "error": info["error"]})
        out[key] = info
    return out


def resolve_deals(
    decisions: Iterable[DealDecision],
    *,
    hierarchy_rows: Iterable[dict],
    client: Optional[HubSpotClient],
    ledger: LedgerLike,
    live_create: bool,
    associate_classes: Iterable[str] = ("deal_candidate",),
) -> list[dict]:
    decisions = list(decisions)
    hierarchy_rows = list(hierarchy_rows)
    by_key = {r["node_key"]: r for r in hierarchy_rows}
    company_map = ledger.company_map(include_pass2=True)
    salvaged = salvage_companies(decisions, hierarchy_rows=hierarchy_rows, client=client, ledger=ledger,
                                 company_map=company_map)
    co_rows = ledger.company_rows()
    note_map = ledger.note_map()
    known = ledger.deal_map()
    results: list[dict] = []
    for d in decisions:
        note_ids = notes_to_associate(d, hierarchy_rows, note_map, classes=associate_classes)
        sal = salvaged.get(d.company_node_key)
        entry = {
            "legacy_library_id": d.legacy_deal_id, "dealname": d.dealname, "anchor_kind": d.anchor_kind,
            "company_name": company_display_name(by_key.get(d.company_node_key)),
            "company_status": sal["company_status"] if sal else company_status_from_ledger(co_rows.get(d.company_node_key)),
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
            entry.update(status=STATUS_NO_COMPANY, error=(sal or {}).get("error") or PASS1_COMPANY_ERROR)
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
