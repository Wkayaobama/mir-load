"""HubSpot-Import-ready CSVs for pass 2 — the deliverable that never depends on the API path.

Whatever the ledger state or the order steps were run in, the operator gets:

  hubspot_deals_import.csv      one row per inferred deal anchor; `Record ID` filled when the API already
                                created the deal (the wizard UPDATES it instead of duplicating), `Company
                                Record ID` filled when the company is resolved (pass 1) or salvaged (pass 2);
                                `Pipeline` / `Deal Stage` deliberately blank — chosen in the wizard, their ids
                                differ between sandbox and production. The card's deals properties follow,
                                by internal name, so the wizard maps them onto what hs-props created.
  hubspot_companies_import.csv  companies referenced by anchors and CONFIRMED missing in the portal
                                (pass-2 salvage searched and found nothing) — import these first, re-run
                                deals-dry (salvage then finds them and fills Company Record ID), then import
                                the deals file.

`review` writes both offline (ids from the ledger when it exists); `deals-dry` / `deals-live` rewrite them
with this run's results overlaid on the ledger.

`ledger-export` regenerates them from the ledger + the decisions file and loads them into BigQuery as
``mrload_raw.hubspot_deals_import`` / ``mrload_raw.hubspot_companies_import`` (snake_case columns, CSV order),
so every execution surface reads the same tables: BigQuery console → Save results → HubSpot Import wizard.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Iterable, Optional

from .card import LibraryCard
from .deal_anchors import DealAnchor, company_display_name
from .deals import COMPANIES_IMPORT_FILE, DEALS_IMPORT_FILE, DealDecision, company_status_from_ledger, default_dealname
from .ledger import LedgerLike
from .properties import PropertySpec, load_property_plan

DEAL_HEAD = ["Record ID", "Deal Name", "Pipeline", "Deal Stage", "Amount", "Deal Description",
             "Company Record ID", "Company Name"]
# bookkeeping for the operator, labelled so they cannot be mistaken for HubSpot properties (map to "Don't import")
DEAL_TAIL = ["Approve (mr-load)", "Company status (mr-load)", "API status (mr-load)", "API error (mr-load)"]
COMPANY_HEAD = ["Company name", "Company Domain Name", "Description"]

# BigQuery materialisation (ledger-export): same rows, snake_case column names, CSV header order (bq load is positional).
# Head columns take the HubSpot INTERNAL property names — the import wizard auto-matches them from a console export;
# bookkeeping columns get an `op_` prefix so they can never be mistaken for the `mrload_*` properties hs-props created.
DEALS_IMPORT_TABLE = "hubspot_deals_import"
COMPANIES_IMPORT_TABLE = "hubspot_companies_import"
BQ_NAME_RX = re.compile(r"^[a-z_][a-z0-9_]*$")
BQ_COLUMN_NAMES = {
    "Record ID": "hs_object_id", "Deal Name": "dealname", "Pipeline": "pipeline", "Deal Stage": "dealstage",
    "Amount": "amount", "Deal Description": "description", "Company Record ID": "company_hs_object_id",
    "Company Name": "company_name",
    "Company name": "name", "Company Domain Name": "domain", "Description": "description",
    "Approve (mr-load)": "op_approve", "Company status (mr-load)": "op_company_status",
    "API status (mr-load)": "op_api_status", "API error (mr-load)": "op_api_error",
}
_BQ_TYPES = {"number": "INT64", "datetime": "TIMESTAMP", "date": "TIMESTAMP"}   # every card `number` is a count


def _plan_fields(card: LibraryCard, object_type: str) -> list[PropertySpec]:
    try:
        plan = load_property_plan(card.raw)
    except ValueError:
        return []
    for o in plan.objects:
        if o.object_type == object_type:
            return list(o.fields)
    return []


def deal_import_columns(card: LibraryCard) -> list[str]:
    return DEAL_HEAD + [f.name for f in _plan_fields(card, "deals")] + DEAL_TAIL


def company_import_columns(card: LibraryCard) -> list[str]:
    return COMPANY_HEAD + [f.name for f in _plan_fields(card, "companies")]


def bigquery_column_name(header: str) -> str:
    """CSV header → BigQuery column: explicit map for the label headers, card names pass through, else sanitised."""
    if header in BQ_COLUMN_NAMES:
        return BQ_COLUMN_NAMES[header]
    name = re.sub(r"[^a-z0-9]+", "_", header.lower()).strip("_") or "column"
    return f"c_{name}" if name[0].isdigit() else name


def _bq_type(spec: Optional[PropertySpec]) -> str:
    return _BQ_TYPES.get(spec.type, "STRING") if spec is not None else "STRING"


def import_table_schemas(card: LibraryCard) -> dict[str, list[dict]]:
    """{table: bq schema} — one NULLABLE field per CSV column, in CSV header order (bq load maps by position)."""
    out: dict[str, list[dict]] = {}
    for table, object_type, columns in ((DEALS_IMPORT_TABLE, "deals", deal_import_columns(card)),
                                        (COMPANIES_IMPORT_TABLE, "companies", company_import_columns(card))):
        specs = {f.name: f for f in _plan_fields(card, object_type)}
        schema = [{"name": bigquery_column_name(c), "type": _bq_type(specs.get(c)), "mode": "NULLABLE"} for c in columns]
        names = [f["name"] for f in schema]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"{table}: duplicate BigQuery column names {dupes} (rename the card field)")
        out[table] = schema
    return out


def write_bigquery_schemas(out_dir: Path, card: LibraryCard) -> dict[str, Path]:
    """<out_dir>/<table>.schema.json for both import tables (card-derived, generated at export time)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for table, schema in import_table_schemas(card).items():
        paths[table] = out_dir / f"{table}.schema.json"
        paths[table].write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    return paths


def _is_file(r: dict) -> bool:
    return str(r.get("is_dir")) in ("False", "false", "0", "")


def _deal_value(column: str, a: DealAnchor, a_row: dict, c_row: dict, hs_deal_id: str, api_status: str,
                unmapped: set[str]) -> object:
    if column in ("legacy_deal_id", "deal_node_key", "anchor_kind", "drive_id", "link",
                  "pdf_count", "deal_candidate_count", "asset_count", "deal_name"):
        return getattr(a, column)
    if column == "segment":
        return a_row.get("inferred_segment") or c_row.get("inferred_segment") or ""
    if column in ("drive_modified_at", "drive_created_at", "owner_email", "depth"):
        return a_row.get(column) or ""
    if column == "legacy_company_id":
        return c_row.get("legacy_library_id") or ""
    if column == "company_node_key":
        return a.company_node_key
    if column == "company_name":
        return company_display_name(c_row)
    if column == "hs_deal_id":
        return hs_deal_id
    if column in ("deal_status", "hs_dealname"):
        return api_status if column == "deal_status" else ""
    unmapped.add(column)
    return ""


def _company_value(column: str, key: str, c_row: dict, counts: dict, unmapped: set[str]) -> object:
    if column == "company_node_key":
        return key
    if column == "legacy_company_id":
        return c_row.get("legacy_library_id") or ""
    if column == "company_name":
        return company_display_name(c_row)
    if column == "segment":
        return c_row.get("inferred_segment") or ""
    if column == "segment_node_key":
        return c_row.get("parent_key") or ""
    if column in ("drive_id", "link", "drive_modified_at", "drive_created_at", "owner_email"):
        return c_row.get(column) or ""
    if column in ("asset_count", "deal_candidate_count", "parked_count", "shortcut_count"):
        return counts.get(column, 0)
    if column in ("hs_company_id", "resolution_status"):
        return ""
    unmapped.add(column)
    return ""


def write_hubspot_import_files(
    rows: Iterable[dict],
    anchors: dict[str, DealAnchor],
    *,
    out_dir: Path,
    card: LibraryCard,
    decisions: Optional[Iterable[DealDecision]] = None,
    results: Optional[Iterable[dict]] = None,
    ledger: Optional[LedgerLike] = None,
) -> dict:
    """Write both import files; this run's ``results`` overlay the ledger; no ledger → ids blank."""
    rows = list(rows)
    by_key = {r["node_key"]: r for r in rows}
    dec_by_id = {d.legacy_deal_id: d for d in (decisions or [])}
    res_by_id = {r["legacy_library_id"]: r for r in (results or [])}
    co_rows = ledger.company_rows() if ledger is not None else {}
    deal_rows = ledger.deal_rows() if ledger is not None else {}
    deal_fields = _plan_fields(card, "deals")
    co_fields = _plan_fields(card, "companies")
    unmapped: set[str] = set()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    def approved(a: DealAnchor) -> bool:
        d = dec_by_id.get(a.legacy_deal_id)
        return bool(d and d.approve)

    deal_lines: list[dict] = []
    missing: dict[str, dict] = {}
    company_status_by_key: dict[str, str] = {}
    for a in sorted(anchors.values(), key=lambda a: (not approved(a), a.company_node_key, a.anchor_kind != "folder", a.deal_name)):
        a_row = by_key.get(a.deal_node_key, {})
        c_row = by_key.get(a.company_node_key, {})
        cname = company_display_name(c_row)
        d = dec_by_id.get(a.legacy_deal_id)
        res = res_by_id.get(a.legacy_deal_id) or {}
        led = deal_rows.get(a.legacy_deal_id) or {}
        co_led = co_rows.get(a.company_node_key)
        hs_deal_id = str(res.get("hs_deal_id") or led.get("hs_deal_id") or "")
        hs_company_id = str(res.get("hs_company_id") or (co_led or {}).get("hs_company_id") or "")
        co_status = res.get("company_status") or company_status_from_ledger(co_led)
        api_status = res.get("status") or led.get("status") or "not_created"
        api_error = res.get("error") or led.get("error") or ""
        line = {
            "Record ID": hs_deal_id,
            "Deal Name": (d.dealname if d and d.dealname else default_dealname(cname, a)),
            "Pipeline": "", "Deal Stage": "",
            "Amount": (d.amount if d and d.amount else ""),
            "Deal Description": f"mr-load inferred deal: {a_row.get('legacy_file_path', '')}/{a_row.get('node_name', a.deal_name)} | {a.link}",
            "Company Record ID": hs_company_id,
            "Company Name": cname,
        }
        for f in deal_fields:
            line[f.name] = _deal_value(f.column, a, a_row, c_row, hs_deal_id, api_status, unmapped)
        line.update({"Approve (mr-load)": "Y" if approved(a) else "N", "Company status (mr-load)": co_status,
                     "API status (mr-load)": api_status, "API error (mr-load)": api_error or ""})
        deal_lines.append(line)
        company_status_by_key[a.company_node_key] = co_status
        if co_status == "missing_in_portal" and c_row:
            missing[a.company_node_key] = c_row

    deal_cols = deal_import_columns(card)
    with (out_dir / DEALS_IMPORT_FILE).open("w", encoding="utf-8", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=deal_cols)
        w.writeheader()
        for line in deal_lines:
            w.writerow({k: ("" if line.get(k) is None else line.get(k)) for k in deal_cols})

    co_cols = company_import_columns(card)
    with (out_dir / COMPANIES_IMPORT_FILE).open("w", encoding="utf-8", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=co_cols)
        w.writeheader()
        for key, c_row in sorted(missing.items(), key=lambda kv: company_display_name(kv[1])):
            beneath = [r for r in rows if _is_file(r) and r.get("company_node_key") == key]
            counts = {
                "asset_count": len(beneath),
                "deal_candidate_count": sum(1 for r in beneath if r.get("asset_class") == "deal_candidate"),
                "parked_count": sum(1 for r in beneath if r.get("asset_class") == "parked_for_review"),
                "shortcut_count": sum(1 for r in beneath if r.get("asset_class") == "shortcut"),
            }
            line = {"Company name": company_display_name(c_row), "Company Domain Name": "",
                    "Description": f"Drive folder: {c_row.get('link') or ''}"}
            for f in co_fields:
                line[f.name] = _company_value(f.column, key, c_row, counts, unmapped)
            w.writerow({k: ("" if line.get(k) is None else line.get(k)) for k in co_cols})

    return {
        DEALS_IMPORT_FILE: len(deal_lines),
        "deals_with_record_id": sum(1 for l in deal_lines if l["Record ID"]),
        "deals_with_company_record_id": sum(1 for l in deal_lines if l["Company Record ID"]),
        "deals_approved": sum(1 for l in deal_lines if l["Approve (mr-load)"] == "Y"),
        COMPANIES_IMPORT_FILE: len(missing),
        "companies_unresolved": sum(1 for st in company_status_by_key.values() if st not in ("resolved", "salvaged")),
        "decisions_without_anchor": sum(1 for lid in dec_by_id if lid not in {a.legacy_deal_id for a in anchors.values()}),
        "unmapped_card_columns": sorted(unmapped),
    }
