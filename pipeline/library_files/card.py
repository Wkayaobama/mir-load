"""Entity-card loader — context/cards/library.yaml → LibraryCard.

The card is the singular map file: scope roots, exclusion patterns, level
roles, asset classification rules and HubSpot gates. The walker, the dbt
project and the runner all read from it so the pipeline cannot drift from
the declared cardinality model.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

DEFAULT_CARD_PATH = Path(__file__).resolve().parents[2] / "context" / "cards" / "library.yaml"

_WHEN_EXT_RX = re.compile(r"extension\s*==\s*(\w+)")
_WHEN_NAME_RX = re.compile(r"name\s*~\s*(.+?)(?:\s+and\s+|$)")
_WHEN_MIME_RX = re.compile(r"mime\s*==\s*(\S+)")


@dataclass(frozen=True)
class ScopeRoot:
    name: str
    drive_id: Optional[str]
    path_prefix: str = ""
    segment_depth: int = 1


@dataclass(frozen=True)
class AssetRule:
    cls: str
    extension: Optional[str] = None
    name_rx: Optional[re.Pattern] = None
    mime: Optional[str] = None
    is_default: bool = False

    def matches(self, *, name: str, extension: str, mime: Optional[str]) -> bool:
        if self.is_default:
            return True
        if self.extension is not None and extension.lower() != self.extension.lower():
            return False
        if self.mime is not None and (mime or "") != self.mime:
            return False
        if self.name_rx is not None and not self.name_rx.search(name):
            return False
        return True


@dataclass
class LibraryCard:
    entity: str
    roots: list[ScopeRoot]
    exclude_patterns: list[re.Pattern] = field(default_factory=list)
    asset_rules: list[AssetRule] = field(default_factory=list)
    gates: dict[str, str] = field(default_factory=dict)
    company_create_gate: str = "MRLOAD_APPROVE_COMPANY_CREATE"
    raw: dict = field(default_factory=dict)

    # -- scope ---------------------------------------------------------------

    def is_excluded_segment(self, segment: str) -> bool:
        return any(rx.search(segment) for rx in self.exclude_patterns)

    def is_excluded_path(self, rel_path: str) -> bool:
        return any(self.is_excluded_segment(s) for s in rel_path.split("/") if s)

    # -- classification ------------------------------------------------------

    def classify_asset(self, *, name: str, mime: Optional[str]) -> str:
        ext = name.rsplit(".", 1)[-1] if "." in name else ""
        for rule in self.asset_rules:
            if rule.matches(name=name, extension=ext, mime=mime):
                return rule.cls
        return "asset"

    def root(self, name_or_id: str) -> Optional[ScopeRoot]:
        for r in self.roots:
            if name_or_id in (r.name, r.drive_id):
                return r
        return None


def _parse_rule(spec: dict) -> AssetRule:
    when = str(spec.get("when", "default")).strip()
    if when == "default":
        return AssetRule(cls=spec["class"], is_default=True)
    ext = _WHEN_EXT_RX.search(when)
    name = _WHEN_NAME_RX.search(when)
    mime = _WHEN_MIME_RX.search(when)
    return AssetRule(
        cls=spec["class"],
        extension=ext.group(1) if ext else None,
        name_rx=re.compile(name.group(1).strip()) if name else None,
        mime=mime.group(1) if mime else None,
    )


def load_library_card(path: Path | None = None) -> LibraryCard:
    p = path or DEFAULT_CARD_PATH
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    scope = data.get("scope", {})
    roots = [
        ScopeRoot(
            name=str(r["name"]),
            drive_id=r.get("drive_id"),
            path_prefix=str(r.get("path_prefix", "")),
            segment_depth=int(r.get("segment_depth", 1)),
        )
        for r in scope.get("roots", [])
    ]
    hub = data.get("hubspot", {})
    return LibraryCard(
        entity=str(data.get("entity", "Library")),
        roots=roots,
        exclude_patterns=[re.compile(x) for x in scope.get("exclude_segments", [])],
        asset_rules=[_parse_rule(r) for r in data.get("asset_classification", [])],
        gates=dict(hub.get("gates", {})),
        company_create_gate=str(
            hub.get("company_resolution", {}).get("create_gate", "MRLOAD_APPROVE_COMPANY_CREATE")
        ),
        raw=data,
    )
