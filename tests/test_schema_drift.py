"""Positional contract: the CSV writer's column order must equal the bq-load schema files (bq load
maps CSV columns by POSITION after skipping the header, so a same-typed reorder would load silently)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from pipeline.library_files.hierarchy import HIERARCHY_COLUMNS
from pipeline.library_files.ledger import LEDGER_TABLES, SqliteLedger

SQL = Path(__file__).resolve().parents[1] / "pipeline" / "library_files" / "sql"


def test_hierarchy_csv_columns_match_bq_schema_in_order():
    schema = json.loads((SQL / "library_hierarchy.schema.json").read_text())
    assert [c["name"] for c in schema] == HIERARCHY_COLUMNS


def test_ledger_export_columns_match_ledger_schemas_in_order(tmp_path: Path):
    ledger = SqliteLedger(tmp_path / "l.sqlite"); ledger.bootstrap()
    con = sqlite3.connect(tmp_path / "l.sqlite")
    for table in LEDGER_TABLES:
        cols = [r[1] for r in con.execute(f"pragma table_info({table})")]
        schema = json.loads((SQL / "ledger" / f"{table}.schema.json").read_text())
        assert [c["name"] for c in schema] == cols, table
