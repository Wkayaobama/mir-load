"""Drive API depth-first walk — the primary bronze source.

One ``files.list`` call per folder (filtered on the parent id, all drives
included) yields children with the fields the index needs, including the
``parents`` array that exposes multi-parent nodes — the cardinality
violation an rclone manifest silently flattens away.

``DriveLister`` is a Protocol so tests inject an in-memory fake; the
``ApiDriveLister`` wraps google-api-python-client and is imported lazily.
Downloads (for the HubSpot attach phase) live here too: ``get_media`` for
binaries, ``export`` for native Google docs.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional, Protocol

from .card import LibraryCard
from .manifest import FOLDER_MIME, SHORTCUT_MIME, ManifestEntry

DRIVE_FIELDS = (
    "nextPageToken, files(id, name, mimeType, parents, size, md5Checksum, "
    "createdTime, modifiedTime, owners(emailAddress, displayName), "
    "webViewLink, shortcutDetails(targetId), trashed)"
)

# Native Google docs cannot be downloaded raw; export to an Office format.
EXPORT_MIME = {
    "application/vnd.google-apps.document":
        ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
    "application/vnd.google-apps.spreadsheet":
        ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
    "application/vnd.google-apps.presentation":
        ("application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"),
    "application/vnd.google-apps.drawing": ("application/pdf", ".pdf"),
}


@dataclass(frozen=True)
class DriveFile:
    id: str
    name: str
    mime_type: str
    parents: tuple[str, ...] = ()
    size: Optional[int] = None
    md5: Optional[str] = None
    created_time: Optional[str] = None
    modified_time: Optional[str] = None
    owner_email: Optional[str] = None
    owner_name: Optional[str] = None
    web_view_link: Optional[str] = None
    shortcut_target_id: Optional[str] = None
    trashed: bool = False

    @property
    def is_folder(self) -> bool:
        return self.mime_type == FOLDER_MIME


class DriveLister(Protocol):
    def get_file(self, file_id: str) -> DriveFile: ...
    def list_children(self, folder_id: str) -> list[DriveFile]: ...
    def download(self, file: DriveFile, dest_dir: Path) -> Path: ...


# -- API implementation ------------------------------------------------------

def _to_drive_file(raw: dict) -> DriveFile:
    owners = raw.get("owners") or []
    owner = owners[0] if owners else {}
    size = raw.get("size")
    return DriveFile(
        id=raw["id"],
        name=raw.get("name", ""),
        mime_type=raw.get("mimeType", ""),
        parents=tuple(raw.get("parents") or ()),
        size=int(size) if size is not None else None,
        md5=raw.get("md5Checksum"),
        created_time=raw.get("createdTime"),
        modified_time=raw.get("modifiedTime"),
        owner_email=owner.get("emailAddress"),
        owner_name=owner.get("displayName"),
        web_view_link=raw.get("webViewLink"),
        shortcut_target_id=(raw.get("shortcutDetails") or {}).get("targetId"),
        trashed=bool(raw.get("trashed")),
    )


class ApiDriveLister:
    """google-api-python-client backed lister (drive.readonly is sufficient)."""

    def __init__(self, service) -> None:
        self._svc = service

    @classmethod
    def from_credentials(cls, credentials_path: Optional[str] = None) -> "ApiDriveLister":
        from googleapiclient.discovery import build  # lazy: optional dependency

        scopes = ["https://www.googleapis.com/auth/drive.readonly"]
        if credentials_path:
            from google.oauth2 import service_account

            creds = service_account.Credentials.from_service_account_file(
                credentials_path, scopes=scopes
            )
        else:
            import google.auth

            creds, _ = google.auth.default(scopes=scopes)
        return cls(build("drive", "v3", credentials=creds, cache_discovery=False))

    def get_file(self, file_id: str) -> DriveFile:
        raw = self._svc.files().get(
            fileId=file_id,
            fields=DRIVE_FIELDS.replace("nextPageToken, files(", "").rstrip(")"),
            supportsAllDrives=True,
        ).execute()
        return _to_drive_file(raw)

    def list_children(self, folder_id: str) -> list[DriveFile]:
        out: list[DriveFile] = []
        token: Optional[str] = None
        while True:
            resp = self._svc.files().list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields=DRIVE_FIELDS,
                pageSize=1000,
                pageToken=token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()
            out.extend(_to_drive_file(r) for r in resp.get("files", []))
            token = resp.get("nextPageToken")
            if not token:
                return out

    def download(self, file: DriveFile, dest_dir: Path) -> Path:
        from googleapiclient.http import MediaIoBaseDownload  # lazy

        dest_dir.mkdir(parents=True, exist_ok=True)
        if file.mime_type in EXPORT_MIME:
            export_mime, ext = EXPORT_MIME[file.mime_type]
            request = self._svc.files().export_media(fileId=file.id, mimeType=export_mime)
            dest = dest_dir / f"{file.id}{ext}"
        else:
            request = self._svc.files().get_media(fileId=file.id, supportsAllDrives=True)
            dest = dest_dir / f"{file.id}_{file.name}"
        with io.FileIO(dest, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        return dest


# -- In-memory fake (tests, offline dry runs) --------------------------------

@dataclass
class FakeDriveLister:
    files: dict[str, DriveFile] = field(default_factory=dict)
    children: dict[str, list[str]] = field(default_factory=dict)
    payloads: dict[str, bytes] = field(default_factory=dict)

    def add(self, file: DriveFile, *, payload: bytes = b"") -> None:
        self.files[file.id] = file
        for p in file.parents:
            self.children.setdefault(p, []).append(file.id)
        if payload:
            self.payloads[file.id] = payload

    def get_file(self, file_id: str) -> DriveFile:
        return self.files[file_id]

    def list_children(self, folder_id: str) -> list[DriveFile]:
        return [self.files[i] for i in self.children.get(folder_id, [])]

    def download(self, file: DriveFile, dest_dir: Path) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{file.id}_{file.name}"
        dest.write_bytes(self.payloads.get(file.id, b""))
        return dest


# -- DFS -----------------------------------------------------------------------

def dfs_entries(
    lister: DriveLister,
    root_id: str,
    *,
    card: Optional[LibraryCard] = None,
    max_depth: Optional[int] = None,
) -> Iterator[ManifestEntry]:
    """Pre-order depth-first walk from ``root_id``; yields ManifestEntry per node.

    Children are visited in name order so runs are deterministic. Excluded
    segments (card.scope.exclude_segments) prune the subtree. A file linked
    into several folders is yielded once per parent path — exactly what Drive
    lists — and carries parents_count > 1 so the cardinality test catches it.
    """

    def _walk(folder_id: str, rel_parent: str, depth: int) -> Iterator[ManifestEntry]:
        if max_depth is not None and depth >= max_depth:
            return
        for child in sorted(lister.list_children(folder_id), key=lambda f: f.name):
            if child.trashed:
                continue
            if card is not None and card.is_excluded_segment(child.name):
                continue
            rel = f"{rel_parent}/{child.name}" if rel_parent else child.name
            yield ManifestEntry(
                path=rel,
                name=child.name,
                is_dir=child.is_folder,
                drive_id=child.id,
                mime_type=child.mime_type,
                size=child.size,
                mod_time=child.modified_time,
                md5=child.md5,
                created_time=child.created_time,
                owner_email=child.owner_email,
                owner_name=child.owner_name,
                web_view_link=child.web_view_link,
                parents_count=len(child.parents) or 1,
                shortcut_target_id=child.shortcut_target_id,
            )
            if child.is_folder:
                yield from _walk(child.id, rel, depth + 1)

    yield from _walk(root_id, "", 0)
