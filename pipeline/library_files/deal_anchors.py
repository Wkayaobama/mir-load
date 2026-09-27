"""Inferred deals — the qualification of level-3 anchor candidates (Python side).

The walker only emits the STRUCTURAL candidate: ``deal_node_key`` = the first folder under a
company folder, inherited by everything beneath it. This module applies the card's heuristic
(``deal_inference``) to hierarchy rows, offline:

  * a folder candidate qualifies when its subtree holds >= min_pdf_in_subtree files with
    extension pdf AND its name is outside the exclusion list (exhibition / tradeshow realm);
  * a ``deal_candidate`` file (PO / Billing PDF) with no folder anchor above it anchors itself.

The same rule lives in dbt (silver_library_deal). Both are exercised by the rehearsal and must
agree; the dbt one is the system of record in BigQuery, this one feeds the offline artefacts
(review queues, deal_decisions.csv, silver preview) and pass 2.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from .card import LibraryCard

ANCHOR_FOLDER = "folder"
ANCHOR_FILE = "file"


@dataclass
class DealAnchor:
    deal_node_key: str
    legacy_deal_id: str
    deal_name: str
    anchor_kind: str                 # folder | file
    company_node_key: str
    link: str = ""
    drive_id: str = ""
    pdf_count: int = 0
    deal_candidate_count: int = 0
    asset_count: int = 0
    file_keys: list[str] = field(default_factory=list)   # every file beneath (or the file itself)


def _is_file(r: dict) -> bool:
    return str(r.get("is_dir")) in ("False", "false", "0", "")


def qualify_deal_anchors(rows: Iterable[dict], card: LibraryCard) -> dict[str, DealAnchor]:
    """{deal_node_key: DealAnchor} for every QUALIFIED anchor (folder or self-anchored file)."""
    rows = list(rows)
    self_classes = set(((card.raw.get("deal_inference") or {}).get("self_anchor_classes")) or ["deal_candidate"])
    folders: dict[str, DealAnchor] = {}
    for r in rows:
        if not _is_file(r) and r.get("deal_node_key") and r["deal_node_key"] == r["node_key"] and r.get("company_node_key"):
            folders[r["node_key"]] = DealAnchor(
                deal_node_key=r["node_key"], legacy_deal_id=r["legacy_library_id"], deal_name=r["node_name"],
                anchor_kind=ANCHOR_FOLDER, company_node_key=r["company_node_key"],
                link=r.get("link") or "", drive_id=r.get("drive_id") or "",
            )
    for r in rows:
        if not _is_file(r):
            continue
        a = folders.get(r.get("deal_node_key") or "")
        if a is None:
            continue
        a.asset_count += 1
        a.file_keys.append(r["node_key"])
        if (r.get("extension") or "").lower() == "pdf":
            a.pdf_count += 1
        if r.get("asset_class") == "deal_candidate":
            a.deal_candidate_count += 1
    out = {k: a for k, a in folders.items()
           if a.pdf_count >= card.deal_min_pdf and not card.is_deal_excluded_name(a.deal_name)}
    for r in rows:   # PO/Billing PDFs directly under the company: the file is its own deal
        if _is_file(r) and r.get("asset_class") in self_classes and r.get("company_node_key") and not r.get("deal_node_key"):
            out[r["node_key"]] = DealAnchor(
                deal_node_key=r["node_key"], legacy_deal_id=r["legacy_library_id"], deal_name=r["node_name"],
                anchor_kind=ANCHOR_FILE, company_node_key=r["company_node_key"],
                link=r.get("link") or "", drive_id=r.get("drive_id") or "",
                pdf_count=1, deal_candidate_count=1, asset_count=1, file_keys=[r["node_key"]],
            )
    return out


def deal_anchor_key_for_row(r: dict, anchors: dict[str, DealAnchor]) -> Optional[str]:
    """The anchor a file row belongs to (its inherited level-3 folder, or itself), if qualified."""
    if r.get("deal_node_key") and r["deal_node_key"] in anchors:
        return r["deal_node_key"]
    if r.get("node_key") in anchors and anchors[r["node_key"]].anchor_kind == ANCHOR_FILE:
        return r["node_key"]
    return None


def deal_documents(rows: Iterable[dict], anchors: dict[str, DealAnchor], *, associated_classes: Iterable[str] = ("deal_candidate",)) -> list[dict]:
    """Files beneath a qualified anchor that pass 2 does NOT associate (deferred note → deal candidates)."""
    assoc = set(associated_classes)
    out = []
    for r in rows:
        if not _is_file(r) or r.get("asset_class") == "shortcut":
            continue
        k = deal_anchor_key_for_row(r, anchors)
        if k and r.get("asset_class") not in assoc:
            out.append({**r, "legacy_deal_id": anchors[k].legacy_deal_id, "deal_name": anchors[k].deal_name})
    return out
