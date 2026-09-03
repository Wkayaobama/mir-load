"""Pass-1 step 1 — turn company folders into HubSpot company objects.

Resolution strategy (card hubspot.company_resolution): exact-name search
first, create only when the MRLOAD_APPROVE_COMPANY_CREATE gate is open.
Outcomes are persisted in the ledger so the attach step can join
company_node_key → hs_company_id and re-runs converge.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from .client import HubSpotClient
from .ledger import LedgerLike

STATUS_LEDGER = "resolved_from_ledger"
STATUS_MATCHED = "matched_by_name"
STATUS_CREATED = "created"
STATUS_WOULD_CREATE = "would_create"
STATUS_DRY_NO_TOKEN = "dry_no_token"
STATUS_AMBIGUOUS = "ambiguous_match"
STATUS_FAILED = "failed"


@dataclass
class CompanyFolder:
    company_node_key: str
    company_name: str
    link: Optional[str] = None
    legacy_library_id: Optional[str] = None


def company_folders_from_hierarchy(rows: Iterable[dict]) -> list[CompanyFolder]:
    out: list[CompanyFolder] = []
    for r in rows:
        if r.get("libr_category") == "company_folder":
            out.append(
                CompanyFolder(
                    company_node_key=r["node_key"],
                    company_name=r["node_name"],
                    link=r.get("link") or None,
                    legacy_library_id=r.get("legacy_library_id") or None,
                )
            )
    return out


def resolve_companies(
    folders: Iterable[CompanyFolder],
    *,
    client: Optional[HubSpotClient],
    ledger: LedgerLike,
    live_create: bool,
    description_from_link: bool = True,
) -> list[dict]:
    known = ledger.company_map()
    results: list[dict] = []
    for f in folders:
        entry = {
            "company_node_key": f.company_node_key, "company_name": f.company_name,
            "hs_company_id": None, "status": None, "error": None,
        }
        if f.company_node_key in known:
            entry.update(hs_company_id=known[f.company_node_key], status=STATUS_LEDGER)
            results.append(entry)
            continue
        if client is None:
            entry["status"] = STATUS_DRY_NO_TOKEN
            results.append(entry)
            continue
        try:
            matches = client.search_companies_by_name(f.company_name)
        except Exception as exc:
            entry.update(status=STATUS_FAILED, error=f"search_error: {exc}")
            results.append(entry)
            ledger.record_company(entry)
            continue
        if len(matches) == 1:
            entry.update(hs_company_id=str(matches[0]["id"]), status=STATUS_MATCHED)
        elif len(matches) > 1:
            entry.update(status=STATUS_AMBIGUOUS, error=f"{len(matches)} companies named {f.company_name!r}")
        elif not live_create:
            entry["status"] = STATUS_WOULD_CREATE
        else:
            try:
                extra = {"description": f"Drive folder: {f.link}"} if (description_from_link and f.link) else {}
                created = client.create_company(name=f.company_name, **extra)
                entry.update(hs_company_id=str(created["id"]), status=STATUS_CREATED)
            except Exception as exc:
                entry.update(status=STATUS_FAILED, error=f"create_error: {exc}")
        results.append(entry)
        ledger.record_company(entry)
    return results
