"""Walker + silver tests against a fixture recreating the live Drive dialects.

The fixture mirrors the structures observed in the Miraex Drive on
2026-08-13: the segment dialect ("Quantum"/<company>) and the engagement
dialect ("2026_Thales"), plus file leaves, and asserts the path-code
derivation reproduces the legacy example (path "30 Sales/20 opportunities
and customer data/quantum" -> code "3020Q").
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.library_files.manifest import load_manifest
from pipeline.library_files.silver_library import SilverIndexBuilder, silver_columns
from pipeline.library_files.walker import (
    CAT_COMPANY,
    CAT_DOCUMENT,
    CAT_ENGAGEMENT,
    CAT_SEGMENT,
    DriveTreeWalker,
    path_code,
)

FIXTURE = [
    {"Path": "Quantum", "Name": "Quantum", "IsDir": True,
     "MimeType": "inode/directory", "ID": "seg-quantum", "ModTime": "2026-08-05T13:13:09Z"},
    {"Path": "Quantum/Thorlabs", "Name": "Thorlabs", "IsDir": True,
     "MimeType": "inode/directory", "ID": "co-thorlabs", "ModTime": "2026-06-29T11:38:33Z"},
    {"Path": "Quantum/Thorlabs/quote_v1.pdf", "Name": "quote_v1.pdf", "IsDir": False,
     "Size": 1024, "MimeType": "application/pdf", "ID": "f-quote",
     "ModTime": "2026-06-30T09:00:00Z", "Hashes": {"md5": "abc123"}},
    {"Path": "2026_Thales", "Name": "2026_Thales", "IsDir": True,
     "MimeType": "inode/directory", "ID": "eng-thales", "ModTime": "2026-07-29T14:31:50Z"},
    {"Path": "2026_Thales/NDA.docx", "Name": "NDA.docx", "IsDir": False,
     "Size": -1, "MimeType": "application/vnd.google-apps.document", "ID": "f-nda",
     "ModTime": "2026-07-29T15:00:00Z"},
]


@pytest.fixture()
def manifest_path(tmp_path: Path) -> Path:
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(FIXTURE), encoding="utf-8")
    return p


def make_walker() -> DriveTreeWalker:
    return DriveTreeWalker(
        path_prefix="30 Sales",
        root_name="20 opportunities and customer data",
    )


def test_path_code_matches_legacy_example():
    assert path_code(
        ["30 Sales", "20 opportunities and customer data", "quantum"]
    ) == "3020Q"


def test_walker_classifies_both_dialects(manifest_path: Path):
    nodes = {n.entry.path: n for n in make_walker().walk(load_manifest(manifest_path))}

    seg = nodes["Quantum"]
    assert seg.category == CAT_SEGMENT
    assert seg.inferred_company_name is None

    co = nodes["Quantum/Thorlabs"]
    assert co.category == CAT_COMPANY
    assert co.inferred_company_name == "Thorlabs"
    assert co.inferred_segment == "Quantum"
    assert co.legacy_file_path == (
        "30 Sales/20 opportunities and customer data/Quantum"
    )
    assert co.path_code == "3020Q"

    leaf = nodes["Quantum/Thorlabs/quote_v1.pdf"]
    assert leaf.category == CAT_DOCUMENT
    assert leaf.inferred_company_name == "Thorlabs"

    eng = nodes["2026_Thales"]
    assert eng.category == CAT_ENGAGEMENT
    assert eng.inferred_deal_name == "2026_Thales"
    assert eng.inferred_company_name == "Thales"
    assert eng.inferred_year == "2026"


def test_silver_rows_carry_parity_columns(manifest_path: Path, tmp_path: Path):
    builder = SilverIndexBuilder(
        make_walker(), owner_email="ops@example.org", owner_fullname="Ops"
    )
    rows = list(builder.build(load_manifest(manifest_path)))
    assert builder.stats.written_rows == len(rows) == 5

    by_name = {r["legacy_file_name"]: r for r in rows}
    thorlabs = by_name["Thorlabs"]
    assert thorlabs["legacy_file_link"] == (
        "https://drive.google.com/drive/folders/co-thorlabs"
    )
    assert thorlabs["libr_type"] == "folder"
    assert thorlabs["legacy_library_id"].startswith("3020Q-")
    assert thorlabs["legacy_company_id"] is None  # FK filled upstream
    # gdoc leaf: size -1 normalised to None
    assert by_name["NDA.docx"]["drive_size"] is None

    cols = silver_columns("mirx")
    assert cols.index("legacy_library_id") == 0
    assert cols[:19][-1] == "loaded_at"
    for r in rows:
        assert set(r) == set(cols)


def test_ids_unique_across_siblings(manifest_path: Path):
    walker = make_walker()
    ids = [
        walker.legacy_library_id(n)
        for n in walker.walk(load_manifest(manifest_path))
    ]
    assert len(ids) == len(set(ids))
