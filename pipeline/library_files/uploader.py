"""Two-phase HubSpot uploader — ported from ic-load, source switched to Drive.

Phase 1 — `upload_phase`: download the asset from Drive on demand (no GCS
          mirror, no rclone), POST /files/v3/files, persist hs_file_id.
Phase 2 — `attach_phase`: POST /crm/v3/objects/notes (hs_attachment_ids)
          then PUT v4 default association note → company.

Idempotency lives on the ledger keyed by legacy_id. Dry-run is the default
at the runner layer (gates MRLOAD_APPROVE_FILES_UPLOAD / _FILE_NOTES_POST).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

import requests

from .client import HubSpotClient
from .drive_walker import DriveFile, DriveLister
from .ledger import LedgerLike


@dataclass
class LibraryFileRow:
    legacy_id: str
    file_name: str
    note_body: str
    target_associations: list[tuple[str, str]] = field(default_factory=list)
    file_path: Optional[Path] = None          # already-local file
    drive_file: Optional[DriveFile] = None    # download on demand


STATUS_PENDING = "pending"
STATUS_UPLOADED = "uploaded"
STATUS_ATTACHED = "attached"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"
STATUS_DRY_RUN = "dry_run"


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, requests.HTTPError):
        status = exc.response.status_code if exc.response is not None else 0
        return status == 429 or 500 <= status < 600
    return isinstance(exc, (requests.ConnectionError, requests.Timeout))


def _retry_after_seconds(exc: Exception) -> float | None:
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        ra = exc.response.headers.get("Retry-After")
        if ra:
            try:
                return float(ra)
            except ValueError:
                return None
    return None


class HubSpotFileUploader:
    def __init__(
        self,
        client: HubSpotClient,
        *,
        lister: Optional[DriveLister] = None,
        cache_dir: Path = Path(".mrload/cache"),
        backoff_schedule: Sequence[float] = (1.0, 2.0, 4.0, 8.0, 16.0),
        sleep_fn: Callable[[float], None] = time.sleep,
        ledger: LedgerLike | None = None,
    ) -> None:
        self.client = client
        self.lister = lister
        self.cache_dir = cache_dir
        self._backoff = tuple(backoff_schedule)
        self._sleep = sleep_fn
        self.ledger = ledger

    def _retry(self, fn: Callable[[], dict]) -> dict:
        last_exc: Exception | None = None
        for attempt, delay in enumerate((0.0, *self._backoff)):
            if delay:
                self._sleep(delay)
            try:
                return fn()
            except Exception as exc:
                last_exc = exc
                if not _is_retryable(exc):
                    raise
                ra = _retry_after_seconds(exc)
                if ra is not None and attempt < len(self._backoff):
                    self._sleep(min(ra, 60.0))
        assert last_exc is not None
        raise last_exc

    def _materialize(self, row: LibraryFileRow) -> Path:
        if row.file_path is not None:
            return row.file_path
        if row.drive_file is None or self.lister is None:
            raise FileNotFoundError("no local path and no Drive source for row")
        return self.lister.download(row.drive_file, self.cache_dir)

    # -- Phase 1 -------------------------------------------------------------

    def upload_phase(self, rows: Iterable[LibraryFileRow], *, live: bool = True) -> list[dict]:
        rows_list = list(rows)
        skip_set = self.ledger.upload_skip_set() if self.ledger else set()
        existing = self.ledger.load_existing([r.legacy_id for r in rows_list]) if self.ledger else {}

        ledger: list[dict] = []
        for row in rows_list:
            entry = {
                "legacy_id": row.legacy_id, "hs_file_id": None, "hs_note_id": None,
                "status": STATUS_PENDING, "error": None, "attempts": 0,
            }
            if row.legacy_id in skip_set:
                prev = existing.get(row.legacy_id, {})
                entry["hs_file_id"] = prev.get("hs_file_id")
                entry["hs_note_id"] = prev.get("hs_note_id")
                entry["status"] = STATUS_UPLOADED
                ledger.append(entry)
                continue

            has_source = (row.file_path is not None and row.file_path.is_file()) or (
                row.drive_file is not None and self.lister is not None
            )
            if not has_source:
                entry["status"] = STATUS_FAILED
                entry["error"] = "file_not_found"
                ledger.append(entry)
                if self.ledger:
                    self.ledger.record_upload(entry)
                continue

            if not live:
                entry["status"] = STATUS_DRY_RUN
                ledger.append(entry)
                if self.ledger:
                    self.ledger.record_upload(entry)
                continue

            try:
                local = self._materialize(row)
                resp = self._retry(
                    lambda: self.client.upload_file(local, file_name=row.file_name)
                )
                entry["hs_file_id"] = resp["id"]
                entry["status"] = STATUS_UPLOADED
            except Exception as exc:
                entry["status"] = STATUS_FAILED
                entry["error"] = f"upload_error: {exc}"
            ledger.append(entry)
            if self.ledger:
                self.ledger.record_upload(entry)
        return ledger

    # -- Phase 2 -------------------------------------------------------------

    def attach_phase(
        self, rows: Iterable[LibraryFileRow], ledger: list[dict], *, live: bool = True
    ) -> list[dict]:
        rows_list = list(rows)
        attach_skip = self.ledger.attach_skip_set() if self.ledger else set()
        existing = self.ledger.load_existing([r.legacy_id for r in rows_list]) if self.ledger else {}

        by_legacy = {e["legacy_id"]: e for e in ledger}
        for row in rows_list:
            entry = by_legacy.get(row.legacy_id)
            if entry is None:
                continue
            if entry["status"] == STATUS_DRY_RUN:
                if self.ledger:
                    self.ledger.record_attach(entry)
                continue
            if entry["status"] != STATUS_UPLOADED:
                continue
            if row.legacy_id in attach_skip:
                prev = existing.get(row.legacy_id, {})
                entry["hs_note_id"] = prev.get("hs_note_id")
                entry["status"] = STATUS_ATTACHED
                continue
            if not row.target_associations:
                entry["status"] = STATUS_FAILED
                entry["error"] = "no_target_associations"
                if self.ledger:
                    self.ledger.record_attach(entry)
                continue
            if not live:
                entry["status"] = STATUS_DRY_RUN
                if self.ledger:
                    self.ledger.record_attach(entry)
                continue

            try:
                note = self._retry(
                    lambda: self.client.create_note(
                        hs_note_body=row.note_body, hs_attachment_ids=[entry["hs_file_id"]]
                    )
                )
                entry["hs_note_id"] = note["id"]
            except Exception as exc:
                entry["status"] = STATUS_FAILED
                entry["error"] = f"note_create_error: {exc}"
                if self.ledger:
                    self.ledger.record_attach(entry)
                continue

            failed_targets: list[str] = []
            for to_type, to_id in row.target_associations:
                try:
                    self._retry(
                        lambda t=to_type, i=to_id: self.client.associate_default(
                            "note", entry["hs_note_id"], t, i
                        )
                    )
                except Exception as exc:
                    failed_targets.append(f"{to_type}:{to_id} ({exc})")
            if failed_targets:
                entry["status"] = STATUS_PARTIAL
                entry["error"] = "association_failed: " + "; ".join(failed_targets)
            else:
                entry["status"] = STATUS_ATTACHED
            if self.ledger:
                self.ledger.record_attach(entry)
        return ledger

    def run(self, rows: list[LibraryFileRow], *, upload_live: bool = True, attach_live: bool = True) -> list[dict]:
        ledger = self.upload_phase(rows, live=upload_live)
        return self.attach_phase(rows, ledger, live=attach_live)
