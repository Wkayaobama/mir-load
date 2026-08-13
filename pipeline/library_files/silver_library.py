"""Silver emission for the mr-load library index.

Counterpart of ic-load's silver_library.py with the flow inverted: there the
bronze CSV already carried the legacy index (path + FK columns) and silver
merely normalised it; here the walker synthesises the index from the Drive
tree, so silver's job is to emit rows in the exact legacy_*/libr_* column
shape the upstream associativity layer (BigQuery -> HubSpot) expects.

Column contract — first 19 columns are drop-in parity with the icalps silver
table (owner-column prefix configurable):

  legacy_library_id, legacy_company_id, legacy_contact_id, legacy_deal_id,
  legacy_case_id, legacy_file_path, legacy_file_name, legacy_file_link,
  libr_note, libr_type, libr_category, libr_status, libr_created_by,
  libr_updated_by, libr_created_at, libr_updated_at,
  <prefix>_owner_email, <prefix>_owner_fullname, loaded_at

The legacy_*_id FK columns are emitted EMPTY by design: no CRM destination
exists yet, so resolution happens upstream. The inference candidates the
walker derived from the hierarchy travel in the trailing inferred_*/drive_*
columns, which is what the associativity layer joins on to fill the FKs.
Consequence: ic-load's at-least-one-FK filter is intentionally NOT applied
at this stage (it would drop every row); pass require_inference=True to
approximate it by dropping rows with no company/deal candidate.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Optional

from .walker import DriveTreeWalker, IndexNode

_PARITY_COLS_TEMPLATE = [
    "legacy_library_id", "legacy_company_id", "legacy_contact_id",
    "legacy_deal_id", "legacy_case_id", "legacy_file_path",
    "legacy_file_name", "legacy_file_link", "libr_note", "libr_type",
    "libr_category", "libr_status", "libr_created_by", "libr_updated_by",
    "libr_created_at", "libr_updated_at", "{p}_owner_email",
    "{p}_owner_fullname", "loaded_at",
]
_EXTRA_COLS = [
    "inferred_segment", "inferred_company_name", "inferred_deal_name",
    "inferred_year", "path_code", "depth", "drive_file_id", "drive_md5",
    "drive_size", "drive_mimetype",
]


def silver_columns(owner_prefix: str = "mirx") -> list[str]:
    return [c.format(p=owner_prefix) for c in _PARITY_COLS_TEMPLATE] + _EXTRA_COLS


@dataclass
class SilverStats:
    total_nodes: int = 0
    written_rows: int = 0
    folders: int = 0
    files: int = 0
    filtered_no_inference: int = 0
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
        require_inference: bool = False,
    ) -> None:
        self.walker = walker
        self.owner_prefix = owner_prefix
        self.owner_email = owner_email
        self.owner_fullname = owner_fullname
        self.id_scheme = id_scheme
        self.note_template = note_template
        self.status = status
        self.require_inference = require_inference
        self.stats = SilverStats()

    def _row(self, node: IndexNode, loaded_at: str) -> Optional[dict]:
        name = node.entry.name.strip()
        if not name or node.legacy_file_path is None:
            self.stats.filtered_missing_path_or_name += 1
            return None
        if self.require_inference and not (
            node.inferred_company_name or node.inferred_deal_name
        ):
            self.stats.filtered_no_inference += 1
            return None
        p = self.owner_prefix
        return {
            "legacy_library_id": self.walker.legacy_library_id(node, scheme=self.id_scheme),
            "legacy_company_id": None,
            "legacy_contact_id": None,
            "legacy_deal_id": None,
            "legacy_case_id": None,
            "legacy_file_path": node.legacy_file_path,
            "legacy_file_name": name,
            "legacy_file_link": node.entry.link,
            "libr_note": self.note_template.format(name=name),
            "libr_type": "folder" if node.entry.is_dir else "file",
            "libr_category": node.category,
            "libr_status": self.status,
            "libr_created_by": None,
            "libr_updated_by": None,
            "libr_created_at": None,  # lsjson has no createdTime; Drive API enrichment fills this
            "libr_updated_at": node.entry.mod_time,
            f"{p}_owner_email": self.owner_email,
            f"{p}_owner_fullname": self.owner_fullname,
            "loaded_at": loaded_at,
            "inferred_segment": node.inferred_segment,
            "inferred_company_name": node.inferred_company_name,
            "inferred_deal_name": node.inferred_deal_name,
            "inferred_year": node.inferred_year,
            "path_code": node.path_code,
            "depth": node.depth,
            "drive_file_id": node.entry.drive_id,
            "drive_md5": node.entry.md5,
            "drive_size": node.entry.size,
            "drive_mimetype": node.entry.mime_type,
        }

    def build(self, entries: Iterable) -> Iterator[dict]:
        self.stats = SilverStats()
        loaded_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f %z")
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
        with out_path.open("w", encoding="utf-8", newline="") as fp:
            writer = csv.DictWriter(fp, fieldnames=cols)
            writer.writeheader()
            for row in self.build(entries):
                writer.writerow(row)
        return self.stats
