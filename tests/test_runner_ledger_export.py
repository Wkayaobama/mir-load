"""ledger-export (dry): the 4 ledger tables + the 2 HubSpot import tables as printed `bq load` commands, nothing run.

The import files are regenerated from the ledger + the decisions overlay into the review dir; their schema JSONs
land in the out dir next to the ledger CSVs. `--tables-only` is the rehearsal's pre-walk call (ledger CSVs only)."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from pipeline.library_files import runner
from pipeline.library_files.ledger import LEDGER_TABLES, SqliteLedger
from tests.test_deals import ELTA, HIER_ELTA, _elta_decision


def _hierarchy_csv(tmp_path: Path) -> Path:
    cols: list[str] = []
    for r in HIER_ELTA:
        cols += [c for c in r if c not in cols]
    p = tmp_path / "library_hierarchy.csv"
    with p.open("w", encoding="utf-8", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=cols)
        w.writeheader()
        w.writerows(HIER_ELTA)
    return p


def _read(p: Path) -> list[dict]:
    return list(csv.DictReader(p.open(encoding="utf-8", newline="")))


def _run(tmp_path: Path, capsys, monkeypatch, *extra: str, hierarchy: Path | None = None):
    monkeypatch.delenv("MRLOAD_APPROVE_BQ_LOAD", raising=False)
    ledger = SqliteLedger(tmp_path / "l.sqlite"); ledger.bootstrap()
    ledger.record_company({"company_node_key": ELTA, "company_name": "ELTA", "hs_company_id": None, "status": "not_in_portal_pass2"})
    rc = runner.main(["ledger-export", "--ledger", str(tmp_path / "l.sqlite"), "--out-dir", str(tmp_path / "ledger_export"),
                      "--dataset", "x", "--hierarchy", str(hierarchy or _hierarchy_csv(tmp_path)),
                      "--review-dir", str(tmp_path / "review"), *extra])
    out = capsys.readouterr()
    return rc, [l for l in out.out.splitlines() if l.startswith("bq load")], out.err


def test_dry_ledger_export_prints_six_loads_and_writes_import_files_and_schemas(tmp_path: Path, capsys, monkeypatch):
    rc, cmds, err = _run(tmp_path, capsys, monkeypatch)
    assert rc == 0 and len(cmds) == 6
    assert [c.split()[6] for c in cmds] == [f"x.{t}" for t in LEDGER_TABLES] + ["x.hubspot_deals_import", "x.hubspot_companies_import"]   # ledger tables first
    assert all("--replace" in c for c in cmds)
    deals = _read(tmp_path / "review" / "hubspot_deals_import.csv")
    comps = _read(tmp_path / "review" / "hubspot_companies_import.csv")
    assert len(deals) == 3 and [c["Company name"] for c in comps] == ["ELTA"]
    assert all(r["Approve (mr-load)"] == "N" for r in deals)                    # no decisions file → template defaults
    assert next(r for r in deals if r["Company Name"] == "ELTA")["Company status (mr-load)"] == "missing_in_portal"
    for table in ("hubspot_deals_import", "hubspot_companies_import"):
        schema_path = tmp_path / "ledger_export" / f"{table}.schema.json"
        assert schema_path.exists() and any(c.endswith(str(schema_path)) for c in cmds)
        assert all(f["mode"] == "NULLABLE" for f in json.loads(schema_path.read_text()))
    assert "hubspot import tables" in err and '"hubspot_deals_import.csv": 3' in err and '"hubspot_companies_import.csv": 1' in err
    assert not (tmp_path / "review" / "hubspot_deals_import.schema.json").exists()   # schemas are load artefacts, not review files


def test_decisions_overlay_marks_approved_rows_and_puts_them_first(tmp_path: Path, capsys, monkeypatch):
    rc, cmds, _ = _run(tmp_path, capsys, monkeypatch, "--decisions", str(_elta_decision(tmp_path, "Y")))
    deals = _read(tmp_path / "review" / "hubspot_deals_import.csv")
    assert rc == 0 and len(cmds) == 6
    assert deals[0]["mrload_legacy_deal_id"] == "3020QE-td" and deals[0]["Approve (mr-load)"] == "Y" and deals[0]["Deal Name"] == "ELTA - Tender"
    assert all(r["Approve (mr-load)"] == "N" for r in deals[1:])


def test_tables_only_skips_the_import_files(tmp_path: Path, capsys, monkeypatch):
    rc, cmds, err = _run(tmp_path, capsys, monkeypatch, "--tables-only")
    assert rc == 0 and len(cmds) == 4 and not (tmp_path / "review").exists()
    assert "hubspot import tables" not in err and not (tmp_path / "ledger_export" / "hubspot_deals_import.schema.json").exists()


def test_missing_hierarchy_exports_the_ledger_tables_and_notes_the_skip(tmp_path: Path, capsys, monkeypatch):
    rc, cmds, err = _run(tmp_path, capsys, monkeypatch, hierarchy=tmp_path / "nope.csv")
    assert rc == 0 and len(cmds) == 4 and "hubspot import tables skipped" in err and not (tmp_path / "review").exists()
