"""Opt-in end-to-end rehearsal (≈30 s): MRLOAD_E2E=1 python -m pytest tests/e2e -q

Drives scripts/e2e_rehearsal.sh (real walker/client/dbt against local mocks)
and asserts the cross-check report is fully green; the dirty scenario must be
stopped by the dbt cardinality gate before any HubSpot write.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(os.environ.get("MRLOAD_E2E") != "1", reason="set MRLOAD_E2E=1 to run")


def _run(scenario: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "SCENARIO": scenario}
    return subprocess.run(["bash", str(REPO / "scripts" / "e2e_rehearsal.sh")], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=600)


def test_clean_scenario_all_checks_pass():
    r = _run("clean")
    report = (REPO / ".mrload" / "rehearsal" / "REPORT.md").read_text()
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-1000:]
    assert "FAIL" not in report and "34/34 checks passed" in report


def test_dirty_scenario_is_stopped_by_the_dbt_gate():
    r = _run("dirty")
    assert r.returncode != 0
    assert "assert_no_multi_parent_nodes" in r.stdout and "companies-dry" not in r.stdout
    ledger = REPO / ".mrload" / "rehearsal" / "ledger.sqlite"
    assert sqlite3.connect(ledger).execute("select count(*) from companies_resolved").fetchone()[0] == 0
