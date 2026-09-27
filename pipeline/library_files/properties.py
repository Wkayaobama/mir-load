"""HubSpot property definitions for the library index — the schema-propagation step.

The pipeline itself writes only built-in HubSpot properties (company name and
description, hs_note_body / hs_attachment_ids / hs_timestamp, dealname…). The
library schema — legacy ids, node keys, asset classes, Drive ids — reaches HubSpot
through StackSync, which syncs the BigQuery silver tables into HubSpot objects,
matched on the record ids that `runner ledger-export` wrote back. StackSync never
creates a property definition, hence two steps around dbt:

  ensure  (before dbt)  create the missing groups/definitions declared in
                        context/cards/library.yaml → hubspot.properties.
                        Idempotent; an existing definition is NEVER modified or
                        deleted, a type conflict is reported as `type_mismatch`.
  verify  (after dbt)   every declared property exists with the declared type AND
                        its source column exists in the silver model as built
                        (dbt/target/catalog.json); then write the StackSync mapping
                        sheet .mrload/review/stacksync_mapping.csv.
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

OBJECT_TYPES = ("companies", "notes", "deals")
HS_TYPES: dict[str, tuple[str, ...]] = {
    "string": ("text", "textarea"),
    "number": ("number",),
    "datetime": ("date",),
    "date": ("date",),
    "bool": ("booleancheckbox",),
    "enumeration": ("select", "radio", "checkbox"),
}
NAME_RX = re.compile(r"^[a-z][a-z0-9_]*$")
OK_STATUSES = ("ok", "ok_hubspot_only")
SHEET_COLUMNS = [
    "object_type", "kind", "hubspot_property", "hubspot_label", "hubspot_type", "hubspot_field_type",
    "bigquery_table", "bigquery_column", "bigquery_type", "status", "note",
]


@dataclass(frozen=True)
class PropertySpec:
    object_type: str
    name: str
    label: str
    type: str
    field_type: str
    column: str
    description: str = ""


@dataclass
class ObjectPlan:
    object_type: str
    source_table: str
    match_column: str
    match_hubspot: str
    fields: list[PropertySpec]


@dataclass
class PropertyPlan:
    group_name: str
    group_label: str
    gate: str
    objects: list[ObjectPlan]

    @property
    def fields(self) -> list[PropertySpec]:
        return [f for o in self.objects for f in o.fields]


class PropertyClientLike(Protocol):
    def list_properties(self, object_type: str) -> list[dict]: ...
    def list_property_groups(self, object_type: str) -> list[dict]: ...
    def create_property_group(self, object_type: str, *, name: str, label: str) -> dict: ...
    def create_property(self, object_type: str, definition: dict) -> dict: ...


# ── plan ─────────────────────────────────────────────────────────────────────

def load_property_plan(card_raw: dict) -> PropertyPlan:
    spec = (card_raw.get("hubspot") or {}).get("properties")
    if not spec:
        raise ValueError("context card has no hubspot.properties section")
    group = spec.get("group") or {}
    objects: list[ObjectPlan] = []
    for object_type, o in (spec.get("objects") or {}).items():
        fields = []
        for f in o.get("fields") or []:
            t = str(f.get("type", "string"))
            default_ft = HS_TYPES.get(t, ("text",))[0]
            fields.append(PropertySpec(
                object_type=str(object_type),
                name=str(f["name"]),
                label=str(f.get("label") or f["name"]),
                type=t,
                field_type=str(f.get("field_type") or default_ft),
                column=str(f.get("column") or f["name"]),
                description=str(f.get("description") or ""),
            ))
        match = o.get("match") or {}
        objects.append(ObjectPlan(
            object_type=str(object_type),
            source_table=str(o["source_table"]),
            match_column=str(match.get("column") or ""),
            match_hubspot=str(match.get("hubspot") or "hs_object_id"),
            fields=fields,
        ))
    plan = PropertyPlan(
        group_name=str(group.get("name") or "mrload_library"),
        group_label=str(group.get("label") or "mr-load library index"),
        gate=str(spec.get("gate") or "MRLOAD_APPROVE_PROPERTY_CREATE"),
        objects=objects,
    )
    problems = validate_plan(plan)
    if problems:
        raise ValueError("hubspot.properties invalid:\n  " + "\n  ".join(problems))
    return plan


def validate_plan(plan: PropertyPlan) -> list[str]:
    problems: list[str] = []
    if not NAME_RX.match(plan.group_name):
        problems.append(f"group name {plan.group_name!r} must match {NAME_RX.pattern}")
    if not plan.objects:
        problems.append("no objects declared")
    for o in plan.objects:
        if o.object_type not in OBJECT_TYPES:
            problems.append(f"{o.object_type}: unknown object type (use {OBJECT_TYPES})")
        if not o.match_column:
            problems.append(f"{o.object_type}: match.column missing (the silver column holding the HubSpot record id)")
        seen: set[str] = set()
        for f in o.fields:
            if not NAME_RX.match(f.name):
                problems.append(f"{o.object_type}.{f.name}: HubSpot internal names are lowercase [a-z0-9_] and start with a letter")
            if f.name in seen:
                problems.append(f"{o.object_type}.{f.name}: declared twice")
            seen.add(f.name)
            if f.type not in HS_TYPES:
                problems.append(f"{o.object_type}.{f.name}: type {f.type!r} not in {sorted(HS_TYPES)}")
            elif f.field_type not in HS_TYPES[f.type]:
                problems.append(f"{o.object_type}.{f.name}: field_type {f.field_type!r} invalid for type {f.type} (allowed {HS_TYPES[f.type]})")
    return problems


def property_definition(plan: PropertyPlan, spec: PropertySpec) -> dict:
    """The POST /crm/v3/properties/{objectType} body."""
    return {
        "name": spec.name,
        "label": spec.label,
        "type": spec.type,
        "fieldType": spec.field_type,
        "groupName": plan.group_name,
        "description": spec.description or f"mr-load library index ← {spec.column}",
        "formField": False,
    }


# ── ensure (before dbt) ──────────────────────────────────────────────────────

def _err_text(exc: Exception) -> str:
    resp = getattr(exc, "response", None)
    if resp is not None:
        try:
            body = resp.text
        except Exception:  # pragma: no cover
            body = ""
        return f"HTTP {resp.status_code}: {body[:240]}"
    return str(exc)[:240]


def ensure_properties(client: PropertyClientLike, plan: PropertyPlan, *, live: bool) -> list[dict]:
    """Create what is missing. Dry (live=False): report only. Never modifies existing definitions."""
    results: list[dict] = []
    for obj in plan.objects:
        existing = {p["name"]: p for p in client.list_properties(obj.object_type)}
        groups = {g["name"] for g in client.list_property_groups(obj.object_type)}
        g_entry = {"object_type": obj.object_type, "kind": "group", "name": plan.group_name, "column": None,
                   "type": None, "status": "exists", "error": None}
        if plan.group_name not in groups:
            if not live:
                g_entry["status"] = "would_create"
            else:
                try:
                    client.create_property_group(obj.object_type, name=plan.group_name, label=plan.group_label)
                    g_entry["status"] = "created"
                except Exception as exc:  # requests.HTTPError or transport error
                    g_entry.update(status="failed", error=_err_text(exc))
        results.append(g_entry)
        for spec in obj.fields:
            entry = {"object_type": obj.object_type, "kind": "property", "name": spec.name, "column": spec.column,
                     "type": spec.type, "status": None, "error": None}
            cur = existing.get(spec.name)
            if cur is not None:
                if cur.get("type") != spec.type:
                    entry.update(status="type_mismatch",
                                 error=f"HubSpot has {cur.get('type')}/{cur.get('fieldType')}, card declares "
                                       f"{spec.type}/{spec.field_type} — not modified")
                else:
                    entry["status"] = "exists"
            elif not live:
                entry["status"] = "would_create"
            else:
                try:
                    client.create_property(obj.object_type, property_definition(plan, spec))
                    entry["status"] = "created"
                except Exception as exc:
                    entry.update(status="failed", error=_err_text(exc))
            results.append(entry)
    return results


# ── verify (after dbt) ───────────────────────────────────────────────────────

def load_catalog(catalog_path: Path) -> dict[str, dict]:
    """dbt/target/catalog.json → {model name: {database, schema, columns: {lower name: type}}}."""
    cat = json.loads(Path(catalog_path).read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for uid, node in (cat.get("nodes") or {}).items():
        if not uid.startswith("model."):
            continue
        meta = node.get("metadata") or {}
        out[str(meta.get("name"))] = {
            "database": meta.get("database"),
            "schema": meta.get("schema"),
            "columns": {str(c).lower(): (v.get("type") if isinstance(v, dict) else None)
                        for c, v in (node.get("columns") or {}).items()},
        }
    return out


def verify_properties(client: Optional[PropertyClientLike], plan: PropertyPlan,
                      catalog: Optional[dict[str, dict]]) -> list[dict]:
    """One row per match key and per property: HubSpot definition state × silver column state."""
    rows: list[dict] = []
    for obj in plan.objects:
        existing = {p["name"]: p for p in client.list_properties(obj.object_type)} if client else None
        model = (catalog or {}).get(obj.source_table)
        table = (f"{model['database']}.{model['schema']}.{obj.source_table}"
                 if model and model.get("database") else obj.source_table)
        cols = model["columns"] if model else None

        def col_type(c: str):
            return cols.get(c.lower()) if cols else None

        m_status = "ok" if cols is None or obj.match_column.lower() in cols else "column_missing"
        if cols is None:
            m_status = "ok_hubspot_only"
        rows.append({
            "object_type": obj.object_type, "kind": "match_key", "hubspot_property": obj.match_hubspot,
            "hubspot_label": "Record ID", "hubspot_type": "number", "hubspot_field_type": "number",
            "bigquery_table": table, "bigquery_column": obj.match_column, "bigquery_type": col_type(obj.match_column),
            "status": m_status,
            "note": "StackSync match key: this column holds the HubSpot record id written back by ledger-export",
        })
        for spec in obj.fields:
            cur = existing.get(spec.name) if existing is not None else None
            if existing is None:
                hs_status, note = "unknown_no_token", "no HubSpot token — definition not checked"
            elif cur is None:
                hs_status, note = "missing", "run hs-props (ensure) first"
            elif cur.get("type") != spec.type:
                hs_status, note = "type_mismatch", f"HubSpot has {cur.get('type')}/{cur.get('fieldType')}"
            else:
                hs_status, note = "ok", ""
            if cols is None:
                c_status = None
            else:
                c_status = "ok" if spec.column.lower() in cols else "column_missing"
                if c_status == "column_missing":
                    note = (note + "; " if note else "") + f"{obj.source_table} has no column {spec.column}"
            if hs_status != "ok":
                status = hs_status
            else:
                status = "ok" if c_status == "ok" else ("ok_hubspot_only" if c_status is None else c_status)
            rows.append({
                "object_type": obj.object_type, "kind": "property", "hubspot_property": spec.name,
                "hubspot_label": spec.label, "hubspot_type": spec.type, "hubspot_field_type": spec.field_type,
                "bigquery_table": table, "bigquery_column": spec.column, "bigquery_type": col_type(spec.column),
                "status": status, "note": note,
            })
    return rows


def write_mapping_sheet(rows: list[dict], out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=SHEET_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in SHEET_COLUMNS})
    return out_path


def summarize(rows: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        out[r["status"]] = out.get(r["status"], 0) + 1
    return out
