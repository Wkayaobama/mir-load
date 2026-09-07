"""Hierarchy emission — the bronze table ``library_hierarchy``.

One row per walked node in the unflatten_hierarchy shape (NodeKey, NodeName,
ParentKey, Depth) plus Drive metadata and the walker's classification. This
is what ``bq load`` ingests; dbt derives every silver table from it and the
cardinality tests run against it.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Optional

from .manifest import ManifestEntry
from .walker import DriveTreeWalker, IndexNode

HIERARCHY_COLUMNS = [
    "node_key", "node_name", "parent_key", "depth", "rel_path",
    "legacy_file_path", "legacy_library_id", "is_dir", "libr_category",
    "asset_class", "company_node_key", "inferred_segment",
    "inferred_company_name", "inferred_deal_name", "inferred_year",
    "path_code", "drive_id", "drive_mimetype", "drive_size", "drive_md5",
    "drive_created_at", "drive_modified_at", "owner_email", "owner_name",
    "link", "parents_count", "extension", "shortcut_target_id", "walked_at",
]


@dataclass
class HierarchyStats:
    total_nodes: int = 0
    folders: int = 0
    files: int = 0
    company_folders: int = 0
    deal_candidates: int = 0
    parked_for_review: int = 0
    multi_parent_nodes: int = 0
    duplicate_node_keys: int = 0
    pruned: int = 0


def hierarchy_row(node: IndexNode, walker: DriveTreeWalker, walked_at: str, *, id_scheme: str) -> dict:
    e: ManifestEntry = node.entry
    return {
        "node_key": node.node_key,
        "node_name": e.name,
        "parent_key": node.parent_key,
        "depth": node.depth,
        "rel_path": e.path,
        "legacy_file_path": node.legacy_file_path,
        "legacy_library_id": walker.legacy_library_id(node, scheme=id_scheme),
        "is_dir": e.is_dir,
        "libr_category": node.category,
        "asset_class": node.asset_class,
        "company_node_key": node.company_node_key,
        "inferred_segment": node.inferred_segment,
        "inferred_company_name": node.inferred_company_name,
        "inferred_deal_name": node.inferred_deal_name,
        "inferred_year": node.inferred_year,
        "path_code": node.path_code,
        "drive_id": e.drive_id,
        "drive_mimetype": e.mime_type,
        "drive_size": e.size,
        "drive_md5": e.md5,
        "drive_created_at": e.created_time,
        "drive_modified_at": e.mod_time,
        "owner_email": e.owner_email,
        "owner_name": e.owner_name,
        "link": e.link,
        "parents_count": e.parents_count,
        "extension": e.extension or None,
        "shortcut_target_id": e.shortcut_target_id,
        "walked_at": walked_at,
    }


class HierarchyWriter:
    def __init__(self, walker: DriveTreeWalker, *, id_scheme: str = "pathcode-hash") -> None:
        self.walker = walker
        self.id_scheme = id_scheme
        self.stats = HierarchyStats()

    def rows(self, entries: Iterable[ManifestEntry]) -> Iterator[dict]:
        self.stats = HierarchyStats()
        walked_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        seen: set[str] = set()
        for node in self.walker.walk(entries):
            s = self.stats
            s.total_nodes += 1
            if node.entry.is_dir:
                s.folders += 1
                if node.category == "company_folder":
                    s.company_folders += 1
            else:
                s.files += 1
                if node.asset_class == "deal_candidate":
                    s.deal_candidates += 1
                elif node.asset_class == "parked_for_review":
                    s.parked_for_review += 1
            if node.entry.parents_count > 1:
                s.multi_parent_nodes += 1
            if node.node_key in seen:
                s.duplicate_node_keys += 1
            seen.add(node.node_key)
            yield hierarchy_row(node, self.walker, walked_at, id_scheme=self.id_scheme)
        self.stats.pruned = self.walker.pruned

    def write_csv(self, entries: Iterable[ManifestEntry], out_path: Path) -> HierarchyStats:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8", newline="") as fp:
            w = csv.DictWriter(fp, fieldnames=HIERARCHY_COLUMNS)
            w.writeheader()
            for row in self.rows(entries):
                w.writerow(row)
        return self.stats


def read_hierarchy_csv(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8", newline="") as fp:
        return list(csv.DictReader(fp))
