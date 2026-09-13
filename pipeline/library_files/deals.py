"""Pass 2 — deal_candidate assets → HubSpot deals, from an operator-approved
decisions file.

Never runs inside the DFS. Input is ``deal_decisions.csv`` produced by
``runner review-export`` and edited by the operator (approve=Y, dealname,
pipeline, dealstage, amount). For each approved row, behind the
MRLOAD_APPROVE_DEAL_CREATE gate:

  1. POST /crm/v3/objects/deals            (dealname, dealstage, pipeline, amount)
  2. PUT v4 default association deal → company   (the asset's anchor company)
  3. PUT v4 default association note → deal      (the pass-1 note, if attached)

Idempotent through ledger.deals_created keyed by legacy_library_id.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from .client import HubSpotClient
from .ledger import LedgerLike

DECISION_COLUMNS = [
    "legacy_library_id", "company_node_key", "company_name", "file_name", "link",
    "asset_class", "dealname", "pipeline", "dealstage", "amount", "approve", "operator_note",
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
    legacy_library_id: str
    company_node_key: str
    dealname: str
    dealstage: str
    pipeline: Optional[str] = None
    amount: Optional[str] = None
    approve: bool = False
    file_name: str = ""


def _truthy(v: object) -> bool:
    return str(v or "").strip().lower() in ("y", "yes", "true", "1")


def read_decisions(path: Path, *, default_pipeline: Optional[str], default_stage: Optional[str]) -> list[DealDecision]:
    out: list[DealDecision] = []
    with path.open("r", encoding="utf-8-sig", newline="") as fp:
        for r in csv.DictReader(fp):
            out.append(
                DealDecision(
                    legacy_library_id=r["legacy_library_id"].strip(),
                    company_node_key=(r.get("company_node_key") or "").strip(),
                    dealname=(r.get("dealname") or "").strip(),
                    dealstage=(r.get("dealstage") or default_stage or "").strip(),
                    pipeline=(r.get("pipeline") or default_pipeline or "").strip() or None,
                    amount=(r.get("amount") or "").strip() or None,
                    approve=_truthy(r.get("approve")),
                    file_name=(r.get("file_name") or "").strip(),
                )
            )
    return out


def write_decisions_template(rows: Iterable[dict], out_path: Path, *, classes: tuple[str, ...] = ("deal_candidate",)) -> int:
    """From hierarchy rows, emit the operator's decisions file (approve=N by default)."""
    company_names = {r["node_key"]: r["node_name"] for r in rows if r.get("libr_category") == "company_folder"}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out_path.open("w", encoding="utf-8", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=DECISION_COLUMNS)
        w.writeheader()
        for r in rows:
            if r.get("asset_class") not in classes or not r.get("company_node_key"):
                continue
            company = company_names.get(r["company_node_key"], "")
            stem = r["node_name"].rsplit(".", 1)[0]
            w.writerow({
                "legacy_library_id": r["legacy_library_id"],
                "company_node_key": r["company_node_key"],
                "company_name": company,
                "file_name": r["node_name"],
                "link": r.get("link") or "",
                "asset_class": r.get("asset_class") or "",
                "dealname": f"{company} - {stem}" if company else stem,
                "pipeline": "",
                "dealstage": "",
                "amount": "",
                "approve": "N",
                "operator_note": "",
            })
            n += 1
    return n


def resolve_deals(
    decisions: Iterable[DealDecision],
    *,
    client: Optional[HubSpotClient],
    ledger: LedgerLike,
    live_create: bool,
) -> list[dict]:
    company_map = ledger.company_map()
    note_map = ledger.note_map()
    known = ledger.deal_map()
    results: list[dict] = []
    for d in decisions:
        entry = {
            "legacy_library_id": d.legacy_library_id, "dealname": d.dealname,
            "hs_deal_id": None, "hs_company_id": company_map.get(d.company_node_key),
            "hs_note_id": note_map.get(d.legacy_library_id), "status": None, "error": None,
        }
        if d.legacy_library_id in known:
            entry.update(hs_deal_id=known[d.legacy_library_id], status=STATUS_LEDGER)
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
        if entry["hs_note_id"]:
            try:
                client.associate_default("note", entry["hs_note_id"], "deal", entry["hs_deal_id"])
            except Exception as exc:
                failures.append(f"note→deal ({exc})")
        entry["status"] = STATUS_PARTIAL if failures else STATUS_CREATED
        entry["error"] = "; ".join(failures) or None
        results.append(entry)
        ledger.record_deal(entry)
    return results
