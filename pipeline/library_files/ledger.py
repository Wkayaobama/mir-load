"""SQLite idempotency ledger — the LedgerLike surface ported from ic-load.

ic-load kept the ledger in the StackSync postgres; mr-load has no such
database on the critical path, so the ledger is a local SQLite file
(stdlib, crash-safe, re-run converges via UPSERT). Three tables:

  files_uploaded      — Phase 1 outcome per legacy_library_id
  file_notes_posted   — Phase 2 outcome per legacy_library_id
  companies_resolved  — company folder → HubSpot company id (pass-1 step 1)
  deals_created       — deal_candidate asset → HubSpot deal id (pass 2)

``export_tables`` dumps every table to CSV so `runner ledger-export` can
push the HubSpot ids back into BigQuery (mrload_raw.*), where dbt joins
them into the silver tables.
"""
from __future__ import annotations

import csv
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Protocol

LEDGER_TABLES = ("files_uploaded", "file_notes_posted", "companies_resolved", "deals_created")


class LedgerLike(Protocol):
    def upload_skip_set(self) -> set[str]: ...
    def attach_skip_set(self) -> set[str]: ...
    def load_existing(self, legacy_ids: Iterable[str]) -> dict[str, dict]: ...
    def record_upload(self, entry: Mapping[str, object]) -> None: ...
    def record_attach(self, entry: Mapping[str, object]) -> None: ...
    def load_attached_rows(self) -> list[dict]: ...
    def record_unattach(self, legacy_id: str, status: str, error: str | None) -> None: ...
    def company_map(self) -> dict[str, str]: ...
    def record_company(self, entry: Mapping[str, object]) -> None: ...
    def note_map(self) -> dict[str, str]: ...
    def deal_map(self) -> dict[str, str]: ...
    def record_deal(self, entry: Mapping[str, object]) -> None: ...


_DDL = """
CREATE TABLE IF NOT EXISTS files_uploaded (
    legacy_library_id TEXT PRIMARY KEY,
    hs_file_id        TEXT,
    status            TEXT NOT NULL,
    error             TEXT,
    attempts          INTEGER NOT NULL DEFAULT 0,
    first_seen_at     TEXT NOT NULL,
    last_attempt_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS file_notes_posted (
    legacy_library_id TEXT PRIMARY KEY,
    hs_note_id        TEXT,
    idempotency_key   TEXT,
    status            TEXT NOT NULL,
    error             TEXT,
    attempts          INTEGER NOT NULL DEFAULT 0,
    first_seen_at     TEXT NOT NULL,
    last_attempt_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS companies_resolved (
    company_node_key  TEXT PRIMARY KEY,
    company_name      TEXT,
    hs_company_id     TEXT,
    status            TEXT NOT NULL,
    error             TEXT,
    resolved_at       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deals_created (
    legacy_library_id TEXT PRIMARY KEY,
    hs_deal_id        TEXT,
    dealname          TEXT,
    hs_company_id     TEXT,
    hs_note_id        TEXT,
    status            TEXT NOT NULL,
    error             TEXT,
    resolved_at       TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SqliteLedger:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def bootstrap(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_DDL)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    # -- read paths ----------------------------------------------------------

    def upload_skip_set(self) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT legacy_library_id FROM files_uploaded WHERE status = 'uploaded'"
            ).fetchall()
        return {r[0] for r in rows}

    def attach_skip_set(self) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT legacy_library_id FROM file_notes_posted WHERE status = 'attached'"
            ).fetchall()
        return {r[0] for r in rows}

    def load_existing(self, legacy_ids: Iterable[str]) -> dict[str, dict]:
        ids = list(legacy_ids)
        if not ids:
            return {}
        out = {i: {"legacy_id": i} for i in ids}
        marks = ",".join("?" * len(ids))
        with self._connect() as conn:
            for lid, fid, st in conn.execute(
                f"SELECT legacy_library_id, hs_file_id, status FROM files_uploaded "
                f"WHERE legacy_library_id IN ({marks})", ids
            ):
                out[lid].update(hs_file_id=fid, upload_status=st)
            for lid, nid, st in conn.execute(
                f"SELECT legacy_library_id, hs_note_id, status FROM file_notes_posted "
                f"WHERE legacy_library_id IN ({marks})", ids
            ):
                out[lid].update(hs_note_id=nid, attach_status=st)
        return out

    # -- write paths ---------------------------------------------------------

    def record_upload(self, entry: Mapping[str, object]) -> None:
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO files_uploaded
                    (legacy_library_id, hs_file_id, status, error, attempts, first_seen_at, last_attempt_at)
                VALUES (?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(legacy_library_id) DO UPDATE SET
                    hs_file_id = excluded.hs_file_id, status = excluded.status,
                    error = excluded.error, attempts = files_uploaded.attempts + 1,
                    last_attempt_at = excluded.last_attempt_at
                """,
                (entry["legacy_id"], entry.get("hs_file_id"), entry["status"], entry.get("error"), now, now),
            )

    def record_attach(self, entry: Mapping[str, object]) -> None:
        now = _now()
        key = entry.get("idempotency_key") or f"mrload_libfile_{entry['legacy_id']}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO file_notes_posted
                    (legacy_library_id, hs_note_id, idempotency_key, status, error, attempts, first_seen_at, last_attempt_at)
                VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(legacy_library_id) DO UPDATE SET
                    hs_note_id = excluded.hs_note_id, idempotency_key = excluded.idempotency_key,
                    status = excluded.status, error = excluded.error,
                    attempts = file_notes_posted.attempts + 1, last_attempt_at = excluded.last_attempt_at
                """,
                (entry["legacy_id"], entry.get("hs_note_id"), key, entry["status"], entry.get("error"), now, now),
            )

    # -- rollback ------------------------------------------------------------

    def load_attached_rows(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT legacy_library_id, hs_note_id, idempotency_key, status "
                "FROM file_notes_posted WHERE status = 'attached'"
            ).fetchall()
        return [
            {"legacy_library_id": r[0], "hs_note_id": r[1], "idempotency_key": r[2], "status": r[3]}
            for r in rows
        ]

    def record_unattach(self, legacy_id: str, status: str, error: str | None) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE file_notes_posted SET status = ?, error = ?, last_attempt_at = ?, "
                "attempts = attempts + 1 WHERE legacy_library_id = ?",
                (status, error, _now(), legacy_id),
            )

    # -- companies -----------------------------------------------------------

    def company_map(self) -> dict[str, str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT company_node_key, hs_company_id FROM companies_resolved "
                "WHERE hs_company_id IS NOT NULL"
            ).fetchall()
        return {r[0]: r[1] for r in rows}

    def record_company(self, entry: Mapping[str, object]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO companies_resolved
                    (company_node_key, company_name, hs_company_id, status, error, resolved_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(company_node_key) DO UPDATE SET
                    company_name = excluded.company_name, hs_company_id = excluded.hs_company_id,
                    status = excluded.status, error = excluded.error, resolved_at = excluded.resolved_at
                """,
                (
                    entry["company_node_key"], entry.get("company_name"), entry.get("hs_company_id"),
                    entry["status"], entry.get("error"), _now(),
                ),
            )

    # -- notes / deals (pass 2) ---------------------------------------------

    def note_map(self) -> dict[str, str]:
        """legacy_library_id → hs_note_id for rows attached in pass 1."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT legacy_library_id, hs_note_id FROM file_notes_posted "
                "WHERE status = 'attached' AND hs_note_id IS NOT NULL"
            ).fetchall()
        return {r[0]: r[1] for r in rows}

    def deal_map(self) -> dict[str, str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT legacy_library_id, hs_deal_id FROM deals_created WHERE hs_deal_id IS NOT NULL"
            ).fetchall()
        return {r[0]: r[1] for r in rows}

    def record_deal(self, entry: Mapping[str, object]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO deals_created
                    (legacy_library_id, hs_deal_id, dealname, hs_company_id, hs_note_id, status, error, resolved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(legacy_library_id) DO UPDATE SET
                    hs_deal_id = excluded.hs_deal_id, dealname = excluded.dealname,
                    hs_company_id = excluded.hs_company_id, hs_note_id = excluded.hs_note_id,
                    status = excluded.status, error = excluded.error, resolved_at = excluded.resolved_at
                """,
                (
                    entry["legacy_library_id"], entry.get("hs_deal_id"), entry.get("dealname"),
                    entry.get("hs_company_id"), entry.get("hs_note_id"), entry["status"],
                    entry.get("error"), _now(),
                ),
            )

    # -- export (step 6: ledger → BigQuery) ----------------------------------

    def export_tables(self, out_dir: Path) -> dict[str, Path]:
        """Dump every ledger table to <out_dir>/<table>.csv (header always written)."""
        out_dir.mkdir(parents=True, exist_ok=True)
        out: dict[str, Path] = {}
        with self._connect() as conn:
            for table in LEDGER_TABLES:
                cur = conn.execute(f"SELECT * FROM {table}")
                cols = [d[0] for d in cur.description]
                path = out_dir / f"{table}.csv"
                with path.open("w", encoding="utf-8", newline="") as fp:
                    w = csv.writer(fp)
                    w.writerow(cols)
                    w.writerows(cur.fetchall())
                out[table] = path
        return out
