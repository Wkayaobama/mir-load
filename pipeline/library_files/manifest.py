"""Bronze source — the rclone manifest of the replicated Drive tree.

``rclone lsjson -R --hash drive:`` emits one JSON array with an entry per
node, relative to the configured root_folder_id. That file is the bronze
layer of mr-load: it is produced by the same tool that performs the byte
copy, so the index can never drift from what was actually cloned.

Entries carry: Path (forward-slash relative path), Name, Size (-1 for
native Google docs), MimeType, ModTime, IsDir, ID (Drive file id) and
Hashes.md5 when --hash was passed. lsjson does not emit webViewLink, so
share links are reconstructed from the id, matching the URL forms the
legacy index stored (drive.google.com/drive/folders/<id> for folders,
drive.google.com/file/d/<id>/view for files).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

FOLDER_MIME = "application/vnd.google-apps.folder"


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

    @property
    def link(self) -> Optional[str]:
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
