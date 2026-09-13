"""Bronze node record — one shape for both walk sources.

Primary source is the Drive API DFS (drive_walker.py), which fills every
field including parents_count, created_time, owners and webViewLink.
Fallback source is an ``rclone lsjson -R --hash`` manifest, which lacks
those fields (they stay None / parents_count=1) — rclone is no longer on
the critical path but the manifest reader is kept for offline runs.

Share links are reconstructed from the Drive id when webViewLink is absent,
in the URL forms the legacy index stored.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

FOLDER_MIME = "application/vnd.google-apps.folder"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"


@dataclass(frozen=True)
class ManifestEntry:
    path: str  # relative to the walked root, forward slashes, no leading /
    name: str
    is_dir: bool
    drive_id: Optional[str]
    mime_type: Optional[str]
    size: Optional[int]  # None when unknown (native Google docs report -1)
    mod_time: Optional[str]
    md5: Optional[str]
    created_time: Optional[str] = None
    owner_email: Optional[str] = None
    owner_name: Optional[str] = None
    web_view_link: Optional[str] = None
    parents_count: int = 1
    shortcut_target_id: Optional[str] = None

    @property
    def extension(self) -> str:
        return self.name.rsplit(".", 1)[-1].lower() if "." in self.name else ""

    @property
    def link(self) -> Optional[str]:
        if self.web_view_link:
            return self.web_view_link
        if not self.drive_id:
            return None
        if self.is_dir:
            return f"https://drive.google.com/drive/folders/{self.drive_id}"
        return f"https://drive.google.com/file/d/{self.drive_id}/view"


def _entry_from_raw(raw: dict) -> ManifestEntry:
    size = raw.get("Size")
    if size is not None and size < 0:
        size = None
    hashes = raw.get("Hashes") or {}
    return ManifestEntry(
        path=str(raw["Path"]).strip("/"),
        name=str(raw.get("Name") or raw["Path"].rsplit("/", 1)[-1]),
        is_dir=bool(raw.get("IsDir")),
        drive_id=raw.get("ID") or None,
        mime_type=raw.get("MimeType") or None,
        size=size,
        mod_time=raw.get("ModTime") or None,
        md5=hashes.get("md5") or hashes.get("MD5") or None,
    )


def load_manifest(manifest_path: Path) -> Iterator[ManifestEntry]:
    """Yields entries from an rclone lsjson file (JSON array or JSONL)."""
    text = manifest_path.read_text(encoding="utf-8-sig").strip()
    if not text:
        return
    if text.startswith("["):
        for raw in json.loads(text):
            yield _entry_from_raw(raw)
        return
    for line in text.splitlines():
        line = line.strip().rstrip(",")
        if line and line.startswith("{"):
            yield _entry_from_raw(json.loads(line))
