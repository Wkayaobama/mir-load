"""Drive-tree walker — reconstructs the hierarchy and infers the datastructure.

The legacy library encoded taxonomy in the path itself; the Miraex Drive
mirrors that convention. Methodical study of the live tree (2026-08-13)
surfaced two dialects under the numbered taxonomy folders:

  segment dialect     "20 Opportunities and customer data" / "Quantum" /
                      "Thorlabs" / ... — an unnumbered *segment* folder whose
                      children are company-named folders (Thorlabs, Toshiba,
                      Bluefors, IQM, ...). A company folder is a library
                      record pointing at the folder link; company FK is
                      inferable from the folder title.

  engagement dialect  "2026_Thales", "2025 IBM", "2023_CSEM MPW", ... —
                      year-prefixed folders that are deal-shaped: the year
                      plus counterparty/program name make the deal candidate,
                      the de-yeared remainder the company candidate.

Path-code derivation replicates the legacy id convention observed in the
icalps index (example: "30 Sales/20 opportunities and customer data/quantum"
-> "3020Q"): each segment contributes its leading ordinal if it has one,
otherwise its first alphanumeric uppercased. The code alone is not unique
across siblings, so legacy_library_id appends a stable hash suffix by
default (scheme "pathcode-hash"); scheme "pathcode" reproduces the bare
code, scheme "hash" mirrors ic-load's fs:<sha1> synthetic ids.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Iterable, Iterator, Optional

from .manifest import ManifestEntry

# Node classification (emitted as libr_category).
CAT_TAXONOMY = "taxonomy"            # numbered folder: "20 Opportunities ..."
CAT_SEGMENT = "segment"              # unnumbered grouping folder: "Quantum"
CAT_COMPANY = "company_folder"       # company-named folder: "Thorlabs"
CAT_ENGAGEMENT = "engagement_folder" # year-prefixed folder: "2026_Thales"
CAT_DOCUMENT = "document"            # any deeper folder or file leaf

_ORDINAL_RX = re.compile(r"^(\d+)\b")
_YEAR_RX = re.compile(r"^((?:19|20)\d{2})[\s_\-]+(.*)$")

# Same exclusion default as ic-load's walker (spec §6).
IMAGE_EXTS: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp", ".svg"}
)


def segment_code(name: str) -> str:
    """"30 Sales" -> "30"; "quantum" -> "Q"; "Thorlabs" -> "T"."""
    m = _ORDINAL_RX.match(name.strip())
    if m:
        return m.group(1)
    for ch in name:
        if ch.isalnum():
            return ch.upper()
    return "X"


def path_code(segments: Iterable[str]) -> str:
    return "".join(segment_code(s) for s in segments if s.strip())


@dataclass
class IndexNode:
    entry: ManifestEntry
    depth: int                      # 1-based, within the walked root
    category: str
    legacy_file_path: str           # legacy-convention parent path
    inferred_segment: Optional[str] = None
    inferred_company_name: Optional[str] = None
    inferred_deal_name: Optional[str] = None
    inferred_year: Optional[str] = None
    parent_rel_path: str = ""
    path_code: str = ""


@dataclass
class _Inference:
    segment: Optional[str] = None
    company: Optional[str] = None
    deal: Optional[str] = None
    year: Optional[str] = None


class DriveTreeWalker:
    """Classifies manifest entries into the inferred library datastructure.

    ``path_prefix``  — legacy ancestry that sits above the walked root
                       (e.g. "30 Sales"), joined into legacy_file_path.
    ``root_name``    — legacy name of the walked root itself
                       (e.g. "20 opportunities and customer data").
    ``segment_depth``— folders at depth <= segment_depth that are neither
                       year-prefixed nor numbered are segments; the first
                       unnumbered folder below that boundary is a company.
    """

    def __init__(
        self,
        *,
        path_prefix: str = "",
        root_name: str = "",
        segment_depth: int = 1,
        exclude_exts: Iterable[str] = (),
    ) -> None:
        self.path_prefix = path_prefix.strip().strip("/")
        self.root_name = root_name.strip().strip("/")
        self.segment_depth = segment_depth
        self._excl = {e.lower() for e in exclude_exts}
        self._inherit: dict[str, _Inference] = {"": _Inference()}

    # -- classification -------------------------------------------------------

    def _classify_folder(self, name: str, depth: int, parent: _Inference) -> tuple[str, _Inference]:
        inf = _Inference(parent.segment, parent.company, parent.deal, parent.year)
        year_m = _YEAR_RX.match(name.strip())
        if year_m:
            inf.deal = name.strip()
            inf.year = year_m.group(1)
            remainder = year_m.group(2).strip(" _-")
            if remainder and not inf.company:
                inf.company = remainder
            return CAT_ENGAGEMENT, inf
        if _ORDINAL_RX.match(name.strip()):
            return CAT_TAXONOMY, inf
        if depth <= self.segment_depth and parent.company is None:
            inf.segment = name.strip()
            return CAT_SEGMENT, inf
        if parent.company is None:
            inf.company = name.strip()
            return CAT_COMPANY, inf
        return CAT_DOCUMENT, inf

    # -- walk -----------------------------------------------------------------

    def _legacy_parent_path(self, rel_parent: str) -> str:
        parts = [self.path_prefix, self.root_name, rel_parent]
        return "/".join(p for p in parts if p)

    def _legacy_segments(self, rel_parent: str) -> list[str]:
        out: list[str] = []
        for chunk in (self.path_prefix, self.root_name, rel_parent):
            out.extend(s for s in chunk.split("/") if s.strip())
        return out

    def walk(self, entries: Iterable[ManifestEntry]) -> Iterator[IndexNode]:
        # Parents precede children in a path-sorted walk, which keeps the
        # single-pass inheritance map valid even if lsjson output arrives
        # unordered.
        for entry in sorted(entries, key=lambda e: e.path):
            parts = entry.path.split("/")
            depth = len(parts)
            rel_parent = "/".join(parts[:-1])
            parent_inf = self._inherit.get(rel_parent, _Inference())

            if entry.is_dir:
                category, inf = self._classify_folder(entry.name, depth, parent_inf)
                self._inherit[entry.path] = inf
            else:
                suffix = "." + entry.name.rsplit(".", 1)[-1].lower() if "." in entry.name else ""
                if suffix in self._excl:
                    continue
                category, inf = CAT_DOCUMENT, parent_inf

            yield IndexNode(
                entry=entry,
                depth=depth,
                category=category,
                legacy_file_path=self._legacy_parent_path(rel_parent),
                inferred_segment=inf.segment,
                inferred_company_name=inf.company if category != CAT_SEGMENT else None,
                inferred_deal_name=inf.deal,
                inferred_year=inf.year,
                parent_rel_path=rel_parent,
                path_code=path_code(self._legacy_segments(rel_parent)),
            )

    # -- id synthesis ---------------------------------------------------------

    def legacy_library_id(self, node: IndexNode, *, scheme: str = "pathcode-hash") -> str:
        full = f"{node.legacy_file_path}/{node.entry.name}"
        digest = hashlib.sha1(full.encode("utf-8")).hexdigest()
        if scheme == "pathcode":
            return node.path_code
        if scheme == "hash":
            return f"dr:{digest[:12]}"
        return f"{node.path_code}-{digest[:8]}"
