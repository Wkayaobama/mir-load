"""Drive API DFS against an in-memory lister: scope pruning, multi-parent
detection, duplicate node keys, asset classes and the hierarchy CSV."""
from __future__ import annotations

from pathlib import Path

from pipeline.library_files.card import load_library_card
from pipeline.library_files.drive_walker import DriveFile, FakeDriveLister, dfs_entries
from pipeline.library_files.hierarchy import HIERARCHY_COLUMNS, HierarchyWriter, read_hierarchy_csv
from pipeline.library_files.manifest import FOLDER_MIME, SHORTCUT_MIME
from pipeline.library_files.walker import DriveTreeWalker

ROOT = "root-20"


def folder(fid: str, name: str, parent: str) -> DriveFile:
    return DriveFile(id=fid, name=name, mime_type=FOLDER_MIME, parents=(parent,),
                     web_view_link=f"https://drive.google.com/drive/folders/{fid}")


def pdf(fid: str, name: str, parent: str, *extra_parents: str) -> DriveFile:
    return DriveFile(id=fid, name=name, mime_type="application/pdf", parents=(parent, *extra_parents),
                     size=10, md5="m", created_time="2026-01-01T00:00:00Z", modified_time="2026-01-02T00:00:00Z",
                     owner_email="owner@miraex.example", owner_name="Owner")


def build_lister() -> FakeDriveLister:
    L = FakeDriveLister()
    L.add(folder("seg-q", "Quantum", ROOT))
    L.add(folder("co-toshiba", "Toshiba", "seg-q"))
    L.add(folder("co-thorlabs", "Thorlabs", "seg-q"))
    L.add(pdf("f-po", "PO_4711.pdf", "co-toshiba"), payload=b"%PDF")
    L.add(pdf("f-spec", "spec.pdf", "co-toshiba"))
    L.add(DriveFile(id="f-doc", name="minutes", mime_type="application/vnd.google-apps.document",
                    parents=("co-toshiba",)))
    L.add(DriveFile(id="f-short", name="link to NDA", mime_type=SHORTCUT_MIME, parents=("co-toshiba",),
                    shortcut_target_id="elsewhere"))
    L.add(pdf("f-multi", "shared.pdf", "co-thorlabs", "co-toshiba"))      # DAG violation
    L.add(pdf("f-loose", "251226 Lead Opportunities.pdf", "seg-q"))        # orphan at segment level
    L.add(folder("seg-70", "70 Tradeshows", ROOT))                          # out of scope
    L.add(pdf("f-booth", "booth.pdf", "seg-70"))
    L.add(folder("dup-a", "Dup", "seg-q"))
    L.add(folder("dup-b", "Dup", "seg-q"))                                  # duplicate sibling name
    return L


def make_walker(card):
    return DriveTreeWalker(path_prefix="30 Sales", root_name="20 opportunities and customer data", card=card)


def test_dfs_prunes_excluded_scope_and_orders_deterministically():
    card = load_library_card()
    paths = [e.path for e in dfs_entries(build_lister(), ROOT, card=card)]
    assert not any(p.startswith("70 Tradeshows") for p in paths)
    assert paths[0] == "Quantum"                                   # pre-order: parent before children
    assert paths.index("Quantum/Dup") < paths.index("Quantum/Thorlabs") < paths.index("Quantum/Toshiba")
    assert paths.index("Quantum/Toshiba") < paths.index("Quantum/Toshiba/PO_4711.pdf")
    assert "Quantum/Toshiba/PO_4711.pdf" in paths


def test_hierarchy_rows_classification_and_flags(tmp_path: Path):
    card = load_library_card()
    entries = list(dfs_entries(build_lister(), ROOT, card=card))
    writer = HierarchyWriter(make_walker(card))
    out = tmp_path / "hierarchy.csv"
    stats = writer.write_csv(entries, out)

    rows = {r["rel_path"]: r for r in read_hierarchy_csv(out)}
    assert list(rows[next(iter(rows))].keys()) == HIERARCHY_COLUMNS

    toshiba = rows["Quantum/Toshiba"]
    assert toshiba["libr_category"] == "company_folder"
    assert toshiba["link"] == "https://drive.google.com/drive/folders/co-toshiba"

    assert rows["Quantum/Toshiba/PO_4711.pdf"]["asset_class"] == "deal_candidate"
    assert rows["Quantum/Toshiba/spec.pdf"]["asset_class"] == "parked_for_review"
    assert rows["Quantum/Toshiba/minutes"]["asset_class"] == "asset"
    assert rows["Quantum/Toshiba/link to NDA"]["asset_class"] == "shortcut"
    for p in ("Quantum/Toshiba/PO_4711.pdf", "Quantum/Toshiba/spec.pdf", "Quantum/Toshiba/minutes"):
        assert rows[p]["company_node_key"] == toshiba["node_key"]

    # a multi-parent file is listed under BOTH parents (as Drive does); both rows are flagged
    assert rows["Quantum/Toshiba/shared.pdf"]["parents_count"] == "2"
    assert rows["Quantum/Thorlabs/shared.pdf"]["parents_count"] == "2"
    assert rows["Quantum/251226 Lead Opportunities.pdf"]["company_node_key"] == ""   # orphan

    assert stats.company_folders == 4          # Toshiba, Thorlabs, Dup, Dup
    assert stats.deal_candidates == 1 and stats.parked_for_review == 4   # spec, shared x2, loose
    assert stats.multi_parent_nodes == 2
    assert stats.duplicate_node_keys == 1
    assert stats.pruned == 0                   # pruned at DFS time, not at walk time


def test_walker_prunes_excluded_paths_from_manifest_source():
    """Exclusion is also applied when entries come from an rclone manifest."""
    from pipeline.library_files.manifest import ManifestEntry
    card = load_library_card()
    w = make_walker(card)
    entries = [
        ManifestEntry(path="70 Tradeshows", name="70 Tradeshows", is_dir=True, drive_id="x",
                      mime_type=FOLDER_MIME, size=None, mod_time=None, md5=None),
        ManifestEntry(path="Quantum", name="Quantum", is_dir=True, drive_id="y",
                      mime_type=FOLDER_MIME, size=None, mod_time=None, md5=None),
    ]
    assert [n.entry.name for n in w.walk(entries)] == ["Quantum"]
    assert w.pruned == 1
