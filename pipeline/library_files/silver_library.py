"""Offline silver preview — the 19-column icalps parity table.

dbt (dbt/models/silver/silver_library_index.sql) is the AUTHORITATIVE
silver layer, built in BigQuery from the hierarchy table with the
cardinality tests. This module renders the same shape locally from a walk
so operators can eyeball the index without a BigQuery round-trip and so
tests pin the column contract.

  legacy_library_id, legacy_company_id, legacy_contact_id, legacy_deal_id,
  legacy_case_id, legacy_file_path, legacy_file_name, legacy_file_link,
  libr_note, libr_type, libr_category, libr_status, libr_created_by,
  libr_updated_by, libr_created_at, libr_updated_at,
  <prefix>_owner_email, <prefix>_owner_fullname, loaded_at

legacy_company_id carries the legacy id of the anchoring company FOLDER
(the N:1 edge). The HubSpot company id is resolved later from the ledger.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Optional

from .walker import CAT_COMPANY, DriveTreeWalker, IndexNode

_PARITY_COLS_TEMPLATE = [
    "legacy_library_id", "legacy_company_id", "legacy_contact_id",
    "legacy_deal_id", "legacy_case_id", "legacy_file_path",
    "legacy_file_name", "legacy_file_link", "libr_note", "libr_type",
    "libr_category", "libr_status", "libr_created_by", "libr_updated_by",
    "libr_created_at", "libr_updated_at", "{p}_owner_email",
    "{p}_owner_fullname", "loaded_at",
]
_EXTRA_COLS = [
    "node_key", "parent_key", "company_node_key", "asset_class",
    "inferred_segment", "inferred_company_name", "inferred_deal_name",
    "inferred_year", "path_code", "depth", "drive_file_id", "drive_md5",
    "drive_size", "drive_mimetype", "parents_count",
]


def silver_columns(owner_prefix: str = "mirx") -> list[str]:
    return [c.format(p=owner_prefix) for c in _PARITY_COLS_TEMPLATE] + _EXTRA_COLS


@dataclass
class SilverStats:
    total_nodes: int = 0
    written_rows: int = 0
    folders: int = 0
    files: int = 0
    filtered_no_anchor: int = 0
    filtered_missing_path_or_name: int = 0


class SilverIndexBuilder:
    def __init__(
        self,
        walker: DriveTreeWalker,
        *,
        owner_prefix: str = "mirx",
        owner_email: Optional[str] = None,
        owner_fullname: Optional[str] = None,
        id_scheme: str = "pathcode-hash",
        note_template: str = "Drive indexed: {name}",
        status: str = "indexed",
        require_anchor: bool = False,
    ) -> None:
        self.walker = walker
        self.owner_prefix = owner_prefix
        self.owner_email = owner_email
        self.owner_fullname = owner_fullname
        self.id_scheme = id_scheme
        self.note_template = note_template
        self.status = status
        self.require_anchor = require_anchor
        self.stats = SilverStats()
        self._company_ids: dict[str, str] = {}

    def _row(self, node: IndexNode, loaded_at: str) -> Optional[dict]:
        name = node.entry.name.strip()
        if not name or node.legacy_file_path is None:
            self.stats.filtered_missing_path_or_name += 1
            return None
        lib_id = self.walker.legacy_library_id(node, scheme=self.id_scheme)
        if node.category == CAT_COMPANY:
            self._company_ids[node.node_key] = lib_id
        company_legacy_id = (
            self._company_ids.get(node.company_node_key) if node.company_node_key else None
        )
        if self.require_anchor and not node.entry.is_dir and company_legacy_id is None:
            self.stats.filtered_no_anchor += 1
            return None
        p = self.owner_prefix
        e = node.entry
        return {
            "legacy_library_id": lib_id,
            "legacy_company_id": company_legacy_id,
            "legacy_contact_id": None,
            "legacy_deal_id": None,
            "legacy_case_id": None,
            "legacy_file_path": node.legacy_file_path,
            "legacy_file_name": name,
            "legacy_file_link": e.link,
            "libr_note": self.note_template.format(name=name),
            "libr_type": "folder" if e.is_dir else "file",
            "libr_category": node.category,
            "libr_status": self.status,
            "libr_created_by": None,
            "libr_updated_by": None,
            "libr_created_at": e.created_time,
            "libr_updated_at": e.mod_time,
            f"{p}_owner_email": e.owner_email or self.owner_email,
            f"{p}_owner_fullname": e.owner_name or self.owner_fullname,
            "loaded_at": loaded_at,
            "node_key": node.node_key,
            "parent_key": node.parent_key,
            "company_node_key": node.company_node_key,
            "asset_class": node.asset_class,
            "inferred_segment": node.inferred_segment,
            "inferred_company_name": node.inferred_company_name,
            "inferred_deal_name": node.inferred_deal_name,
            "inferred_year": node.inferred_year,
            "path_code": node.path_code,
            "depth": node.depth,
            "drive_file_id": e.drive_id,
            "drive_md5": e.md5,
            "drive_size": e.size,
            "drive_mimetype": e.mime_type,
            "parents_count": e.parents_count,
        }

    def build(self, entries: Iterable) -> Iterator[dict]:
        self.stats = SilverStats()
        self._company_ids = {}
        loaded_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        for node in self.walker.walk(entries):
            self.stats.total_nodes += 1
            row = self._row(node, loaded_at)
            if row is None:
                continue
            self.stats.written_rows += 1
            if node.entry.is_dir:
                self.stats.folders += 1
            else:
                self.stats.files += 1
            yield row

    def write_csv(self, entries: Iterable, out_path: Path) -> SilverStats:
        cols = silver_columns(self.owner_prefix)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8", newline="") as fp:
            writer = csv.DictWriter(fp, fieldnames=cols)
            writer.writeheader()
            for row in self.build(entries):
                writer.writerow(row)
        return self.stats
