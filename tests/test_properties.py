"""Schema propagation into HubSpot: property plan (card), ensure (before dbt), verify + mapping sheet (after dbt)."""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import pytest

from pipeline.library_files.card import load_library_card
from pipeline.library_files.properties import (
    NAME_RX, SHEET_COLUMNS, ensure_properties, load_catalog, load_property_plan, property_definition,
    summarize, validate_plan, verify_properties, write_mapping_sheet,
)

REPO = Path(__file__).resolve().parents[1]


class FakeHubSpot:
    """In-memory stand-in for the four property endpoints; records every create."""

    def __init__(self, existing=None, groups=None, fail_create_for=()):
        self.props = {o: dict(v) for o, v in (existing or {}).items()}
        self.groups = {o: set(v) for o, v in (groups or {}).items()}
        self.created: list[tuple[str, str]] = []
        self.groups_created: list[tuple[str, str]] = []
        self.fail_create_for = set(fail_create_for)

    def list_properties(self, object_type):
        return list(self.props.get(object_type, {}).values())

    def list_property_groups(self, object_type):
        return [{"name": g} for g in self.groups.get(object_type, set())]

    def create_property_group(self, object_type, *, name, label):
        self.groups.setdefault(object_type, set()).add(name)
        self.groups_created.append((object_type, name))
        return {"name": name, "label": label}

    def create_property(self, object_type, definition):
        if definition["name"] in self.fail_create_for:
            raise RuntimeError("HTTP 403: This app hasn't been granted all required scopes (crm.schemas.notes.write)")
        assert definition["groupName"] in self.groups.get(object_type, set()), "group must exist before its properties"
        self.props.setdefault(object_type, {})[definition["name"]] = dict(definition)
        self.created.append((object_type, definition["name"]))
        return definition


@pytest.fixture
def plan():
    return load_property_plan(load_library_card().raw)


def test_card_plan_is_valid_and_covers_the_three_objects(plan):
    assert [o.object_type for o in plan.objects] == ["companies", "notes", "deals"]
    assert plan.gate == "MRLOAD_APPROVE_PROPERTY_CREATE" and NAME_RX.match(plan.group_name)
    assert validate_plan(plan) == []
    for o in plan.objects:
        assert o.match_column.startswith("hs_") and o.match_hubspot == "hs_object_id" and o.fields
    assert all(f.name.startswith("mrload_") for f in plan.fields)


def test_every_mapped_column_is_named_in_its_silver_model(plan):
    """Cheap drift guard at unit level; the dbt catalog check in hs-props-verify is the real one."""
    yml = (REPO / "dbt/models/silver/_silver__models.yml").read_text()
    index_sql = (REPO / "dbt/models/silver/silver_library_index.sql").read_text()
    for o in plan.objects:
        text = (REPO / "dbt/models/silver" / f"{o.source_table}.sql").read_text() + yml
        if o.source_table == "silver_library_deal_candidates":   # select i.* from the index
            text += index_sql
        words = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text))
        missing = [c for c in [f.column for f in o.fields] + [o.match_column] if c not in words]
        assert not missing, f"{o.source_table}: card maps columns the model does not name: {missing}"


def test_validate_rejects_bad_names_types_and_duplicates():
    raw = {"hubspot": {"properties": {"group": {"name": "Bad Group"}, "objects": {
        "tickets": {"source_table": "x", "match": {"column": "hs_id"}, "fields": [{"name": "ok_name"}]},
        "notes": {"source_table": "silver_library_index", "match": {}, "fields": [
            {"name": "Mixed-Case", "column": "a"},
            {"name": "dup", "column": "a"}, {"name": "dup", "column": "b"},
            {"name": "badtype", "type": "money"},
            {"name": "badft", "type": "number", "field_type": "text"},
        ]}}}}}
    with pytest.raises(ValueError) as exc:
        load_property_plan(raw)
    msg = str(exc.value)
    for needle in ("group name", "tickets", "match.column", "Mixed-Case", "declared twice", "money", "badft"):
        assert needle in msg, needle


def test_ensure_dry_reports_and_creates_nothing(plan):
    hs = FakeHubSpot()
    res = ensure_properties(hs, plan, live=False)
    assert hs.created == [] and hs.groups_created == []
    assert summarize(res) == {"would_create": len(plan.fields) + len(plan.objects)}


def test_ensure_live_creates_missing_skips_existing_flags_mismatch_and_is_idempotent(plan):
    hs = FakeHubSpot(
        existing={"companies": {
            "mrload_drive_link": {"name": "mrload_drive_link", "type": "string", "fieldType": "text", "label": "seeded"},
            "mrload_asset_count": {"name": "mrload_asset_count", "type": "string", "fieldType": "text"},
        }},
        groups={"companies": {"mrload_library"}},
    )
    res = ensure_properties(hs, plan, live=True)
    status = {(r["object_type"], r["kind"], r["name"]): r["status"] for r in res}
    assert status[("companies", "property", "mrload_drive_link")] == "exists"
    assert status[("companies", "property", "mrload_asset_count")] == "type_mismatch"
    assert status[("companies", "group", "mrload_library")] == "exists"
    assert status[("notes", "group", "mrload_library")] == status[("deals", "group", "mrload_library")] == "created"
    assert len(hs.created) == len(plan.fields) - 2
    assert hs.props["companies"]["mrload_asset_count"]["type"] == "string"          # never modified
    assert hs.props["companies"]["mrload_drive_link"]["label"] == "seeded"           # never re-created
    before = len(hs.created)
    res2 = ensure_properties(hs, plan, live=True)
    assert len(hs.created) == before
    assert {r["status"] for r in res2} == {"exists", "type_mismatch"}


def test_ensure_reports_failed_create_with_error_and_continues(plan):
    hs = FakeHubSpot(fail_create_for={"mrload_legacy_company_id"})
    res = ensure_properties(hs, plan, live=True)
    failed = [r for r in res if r["status"] == "failed"]
    assert {r["name"] for r in failed} == {"mrload_legacy_company_id"} and len(failed) == 3   # companies + notes + deals
    assert all("403" in r["error"] and "scopes" in r["error"] for r in failed)
    assert len(hs.created) == len(plan.fields) - 3


def test_property_definition_body(plan):
    spec = next(f for f in plan.fields if f.type == "datetime")
    body = property_definition(plan, spec)
    assert body == {"name": spec.name, "label": spec.label, "type": "datetime", "fieldType": "date",
                    "groupName": plan.group_name, "description": body["description"], "formField": False}
    assert body["description"]


def test_verify_flags_missing_definition_and_missing_column_and_writes_sheet(plan, tmp_path):
    hs = FakeHubSpot(groups={o.object_type: {plan.group_name} for o in plan.objects})
    ensure_properties(hs, plan, live=True)
    del hs.props["notes"]["mrload_drive_md5"]
    catalog = {o.source_table: {"database": "proj", "schema": "mrload",
                                "columns": {f.column.lower(): "STRING" for f in o.fields} | {o.match_column: "STRING"}}
               for o in plan.objects}
    del catalog["silver_library_company"]["columns"]["segment"]
    rows = verify_properties(hs, plan, catalog)
    status = {(r["object_type"], r["hubspot_property"]): r["status"] for r in rows}
    assert status[("notes", "mrload_drive_md5")] == "missing"
    assert status[("companies", "mrload_segment")] == "column_missing"
    assert status[("companies", "hs_object_id")] == "ok"
    assert summarize(rows) == {"ok": len(rows) - 2, "missing": 1, "column_missing": 1}
    sheet = write_mapping_sheet(rows, tmp_path / "stacksync_mapping.csv")
    got = list(csv.DictReader(sheet.open(encoding="utf-8")))
    assert list(got[0].keys()) == SHEET_COLUMNS and len(got) == len(rows)
    assert got[0]["kind"] == "match_key" and got[0]["bigquery_table"] == "proj.mrload.silver_library_company"
    assert got[0]["bigquery_column"] == "hs_company_id" and got[0]["hubspot_property"] == "hs_object_id"


def test_verify_without_catalog_or_token_degrades_explicitly(plan):
    rows = verify_properties(None, plan, None)
    assert {r["status"] for r in rows if r["kind"] == "property"} == {"unknown_no_token"}
    assert {r["status"] for r in rows if r["kind"] == "match_key"} == {"ok_hubspot_only"}
    assert all(r["bigquery_table"] == o for r, o in zip(rows[:1], ["silver_library_company"]))


def test_load_catalog_reads_the_dbt_shape(tmp_path):
    cat = {"nodes": {
        "model.mr_load_library.silver_library_company": {
            "metadata": {"name": "silver_library_company", "database": "p", "schema": "mrload"},
            "columns": {"Company_Node_Key": {"type": "STRING"}, "asset_count": {"type": "INT64"}}},
        "source.mr_load_library.mrload_raw.library_hierarchy": {"metadata": {"name": "library_hierarchy"}, "columns": {}},
    }}
    p = tmp_path / "catalog.json"
    p.write_text(json.dumps(cat))
    c = load_catalog(p)
    assert list(c) == ["silver_library_company"]
    assert c["silver_library_company"]["columns"] == {"company_node_key": "STRING", "asset_count": "INT64"}
