"""Tree walker — reconstructs the hierarchy and applies the entity card.

The legacy library encoded taxonomy in the path itself; the Miraex Drive
mirrors that convention. Within the 30 Sales domain (see
context/cards/library.yaml) the walked root "20 opportunities and customer
data" holds *segment* folders (Quantum, ...) whose children are
company-named folders (Thorlabs, Toshiba, ...). Every file beneath a
company folder is a library asset anchored to that company.

Two dialects are still recognised so the grammar stays valid for other
roots: year-prefixed *engagement* folders ("2026_Thales") are deal-shaped
and numbered folders are taxonomy. Only the segment/company dialect is in
scope for pass 1.

Node keys mirror ic-load's unflatten_hierarchy: NodeKey = "|".join(path),
ParentKey = key of the parent, Depth = level. Path-code derivation
replicates the legacy id convention ("30 Sales/20 opportunities and
customer data/quantum" -> "3020Q"); the code is not unique across siblings,
so legacy_library_id appends a stable hash suffix by default.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Iterable, Iterator, Optional

from .card import LibraryCard
from .manifest import SHORTCUT_MIME, ManifestEntry

# Node classification (emitted as libr_category).
CAT_TAXONOMY = "taxonomy"            # numbered folder: "20 Opportunities ..."
CAT_SEGMENT = "segment"              # unnumbered grouping folder: "Quantum"
CAT_COMPANY = "company_folder"       # company-named folder: "Thorlabs"
CAT_ENGAGEMENT = "engagement_folder" # year-prefixed folder: "2026_Thales"
CAT_DOCUMENT = "document"            # any deeper folder or file leaf

# Asset classes (files only, emitted as asset_class) — see card asset_classification.
ASSET = "asset"
ASSET_DEAL_CANDIDATE = "deal_candidate"
ASSET_PARKED = "parked_for_review"
ASSET_SHORTCUT = "shortcut"

_ORDINAL_RX = re.compile(r"^(\d+)\b")
_YEAR_RX = re.compile(r"^((?:19|20)\d{2})[\s_\-]+(.*)$")

# Same exclusion default as ic-load's walker (spec §6).
IMAGE_EXTS: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp", ".svg"}
)

KEY_SEP = "|"


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


def node_key(segments: Iterable[str]) -> str:
    return KEY_SEP.join(s.strip() for s in segments if s.strip())


@dataclass
class IndexNode:
    entry: ManifestEntry
    depth: int                      # 1-based, within the walked root
    category: str
    legacy_file_path: str           # legacy-convention parent path
    node_key: str
    parent_key: Optional[str]
    inferred_segment: Optional[str] = None
    inferred_company_name: Optional[str] = None
    inferred_deal_name: Optional[str] = None
    inferred_year: Optional[str] = None
    company_node_key: Optional[str] = None   # the N:1 anchor edge (Library → Company)
    asset_class: Optional[str] = None        # files only
    parent_rel_path: str = ""
    path_code: str = ""


@dataclass
class _Inference:
    segment: Optional[str] = None
    company: Optional[str] = None
    company_key: Optional[str] = None
    deal: Optional[str] = None
    year: Optional[str] = None


class DriveTreeWalker:
    """Classifies entries into the inferred library datastructure.

    ``path_prefix``  — legacy ancestry above the walked root (e.g. "30 Sales").
    ``root_name``    — legacy name of the walked root itself.
    ``segment_depth``— folders at depth <= segment_depth that are neither
                       year-prefixed nor numbered are segments; the first
                       unnumbered folder below that boundary is a company.
    ``card``         — supplies scope exclusions and asset classification;
                       excluded subtrees are pruned (never emitted).
    """

    def __init__(
        self,
        *,
        path_prefix: str = "",
        root_name: str = "",
        segment_depth: int = 1,
        exclude_exts: Iterable[str] = (),
        card: Optional[LibraryCard] = None,
    ) -> None:
        self.path_prefix = path_prefix.strip().strip("/")
        self.root_name = root_name.strip().strip("/")
        self.segment_depth = segment_depth
        self.card = card
        self._excl = {e.lower() for e in exclude_exts}
        self._inherit: dict[str, _Inference] = {"": _Inference()}
        self.pruned: int = 0

    # -- classification -------------------------------------------------------

    def _classify_folder(
        self, name: str, depth: int, parent: _Inference, key: str
    ) -> tuple[str, _Inference]:
        inf = _Inference(
            parent.segment, parent.company, parent.company_key, parent.deal, parent.year
        )
        year_m = _YEAR_RX.match(name.strip())
        if year_m:
            inf.deal = name.strip()
            inf.year = year_m.group(1)
            remainder = year_m.group(2).strip(" _-")
            if remainder and not inf.company:
                inf.company = remainder
                inf.company_key = key
            return CAT_ENGAGEMENT, inf
        if _ORDINAL_RX.match(name.strip()):
            return CAT_TAXONOMY, inf
        if depth <= self.segment_depth and parent.company is None:
            inf.segment = name.strip()
            return CAT_SEGMENT, inf
        if parent.company is None:
            inf.company = name.strip()
            inf.company_key = key
            return CAT_COMPANY, inf
        return CAT_DOCUMENT, inf

    def _classify_asset(self, entry: ManifestEntry) -> str:
        if entry.mime_type == SHORTCUT_MIME:
            return ASSET_SHORTCUT
        if self.card is not None:
            return self.card.classify_asset(name=entry.name, mime=entry.mime_type)
        return ASSET

    # -- walk -----------------------------------------------------------------

    def _legacy_segments(self, rel_parent: str) -> list[str]:
        out: list[str] = []
        for chunk in (self.path_prefix, self.root_name, rel_parent):
            out.extend(s for s in chunk.split("/") if s.strip())
        return out

    def walk(self, entries: Iterable[ManifestEntry]) -> Iterator[IndexNode]:
        # Parents precede children in a path-sorted walk, which keeps the
        # single-pass inheritance map valid even if input arrives unordered.
        for entry in sorted(entries, key=lambda e: e.path):
            if self.card is not None and self.card.is_excluded_path(entry.path):
                self.pruned += 1
                continue
            parts = entry.path.split("/")
            depth = len(parts)
            rel_parent = "/".join(parts[:-1])
            parent_inf = self._inherit.get(rel_parent, _Inference())
            legacy_parent_segments = self._legacy_segments(rel_parent)
            key = node_key(legacy_parent_segments + [entry.name])
            parent_key = node_key(legacy_parent_segments) if depth > 1 else None

            asset_class: Optional[str] = None
            if entry.is_dir:
                category, inf = self._classify_folder(entry.name, depth, parent_inf, key)
                self._inherit[entry.path] = inf
            else:
                if ("." + entry.extension) in self._excl:
                    continue
                category, inf = CAT_DOCUMENT, parent_inf
                asset_class = self._classify_asset(entry)

            yield IndexNode(
                entry=entry,
                depth=depth,
                category=category,
                legacy_file_path="/".join(legacy_parent_segments),
                node_key=key,
                parent_key=parent_key,
                inferred_segment=inf.segment,
                inferred_company_name=inf.company if category != CAT_SEGMENT else None,
                inferred_deal_name=inf.deal,
                inferred_year=inf.year,
                company_node_key=inf.company_key if category != CAT_SEGMENT else None,
                asset_class=asset_class,
                parent_rel_path=rel_parent,
                path_code=path_code(legacy_parent_segments),
            )

    # -- id synthesis ---------------------------------------------------------

    def legacy_library_id(self, node: IndexNode, *, scheme: str = "pathcode-hash") -> str:
        digest = hashlib.sha1(node.node_key.encode("utf-8")).hexdigest()
        if scheme == "pathcode":
            return node.path_code
        if scheme == "hash":
            return f"dr:{digest[:12]}"
        return f"{node.path_code}-{digest[:8]}"
