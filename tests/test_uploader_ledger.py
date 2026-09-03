"""Company resolution + two-phase attach against a fake HubSpot client and the
SQLite ledger: dry-run default, live path, idempotent re-run."""
from __future__ import annotations

from pathlib import Path

from pipeline.library_files.companies import CompanyFolder, resolve_companies
from pipeline.library_files.drive_walker import DriveFile, FakeDriveLister
from pipeline.library_files.ledger import SqliteLedger
from pipeline.library_files.uploader import (
    STATUS_ATTACHED, STATUS_DRY_RUN, STATUS_UPLOADED, HubSpotFileUploader, LibraryFileRow,
)


class FakeClient:
    def __init__(self, companies: dict[str, list[dict]] | None = None) -> None:
        self.calls: list[tuple] = []
        self.companies = companies or {}
        self._n = 0

    def _next(self) -> str:
        self._n += 1
        return str(1000 + self._n)

    def search_companies_by_name(self, name: str, *, limit: int = 5):
        self.calls.append(("search", name))
        return self.companies.get(name, [])

    def create_company(self, *, name: str, **extra):
        self.calls.append(("create_company", name, extra))
        return {"id": self._next()}

    def upload_file(self, path: Path, *, file_name=None, **kw):
        self.calls.append(("upload", path.name, file_name))
        return {"id": self._next()}

    def create_note(self, *, hs_note_body, hs_attachment_ids, **kw):
        self.calls.append(("note", hs_note_body, list(hs_attachment_ids)))
        return {"id": self._next()}

    def associate_default(self, f, fid, t, tid):
        self.calls.append(("assoc", f, fid, t, tid))
        return {}


def test_resolve_companies_dry_then_live(tmp_path: Path):
    ledger = SqliteLedger(tmp_path / "ledger.sqlite")
    ledger.bootstrap()
    folders = [
        CompanyFolder("30 Sales|20 opp|Quantum|Toshiba", "Toshiba", link="https://drive/x"),
        CompanyFolder("30 Sales|20 opp|Quantum|Thorlabs", "Thorlabs"),
    ]
    client = FakeClient(companies={"Thorlabs": [{"id": "77"}]})

    dry = resolve_companies(folders, client=client, ledger=ledger, live_create=False)
    assert {r["company_name"]: r["status"] for r in dry} == {
        "Toshiba": "would_create", "Thorlabs": "matched_by_name",
    }
    assert ledger.company_map() == {"30 Sales|20 opp|Quantum|Thorlabs": "77"}

    live = resolve_companies(folders, client=client, ledger=ledger, live_create=True)
    statuses = {r["company_name"]: r["status"] for r in live}
    assert statuses == {"Toshiba": "created", "Thorlabs": "resolved_from_ledger"}
    assert ("create_company", "Toshiba", {"description": "Drive folder: https://drive/x"}) in client.calls
    assert len(ledger.company_map()) == 2


def _rows() -> list[LibraryFileRow]:
    return [
        LibraryFileRow(
            legacy_id="3020QT-aaaa", file_name="PO_4711.pdf", note_body="30 Sales/.../PO_4711.pdf",
            target_associations=[("company", "1001")],
            drive_file=DriveFile(id="f-po", name="PO_4711.pdf", mime_type="application/pdf"),
        )
    ]


def test_attach_dry_run_by_default(tmp_path: Path):
    client = FakeClient()
    ledger = SqliteLedger(tmp_path / "l.sqlite"); ledger.bootstrap()
    lister = FakeDriveLister(); lister.add(DriveFile(id="f-po", name="PO_4711.pdf", mime_type="application/pdf"), payload=b"%PDF")
    up = HubSpotFileUploader(client, lister=lister, cache_dir=tmp_path / "cache", ledger=ledger, sleep_fn=lambda s: None)
    out = up.run(_rows(), upload_live=False, attach_live=False)
    assert out[0]["status"] == STATUS_DRY_RUN
    assert client.calls == []                     # nothing fired


def test_attach_live_then_idempotent_rerun(tmp_path: Path):
    client = FakeClient()
    ledger = SqliteLedger(tmp_path / "l.sqlite"); ledger.bootstrap()
    lister = FakeDriveLister(); lister.add(DriveFile(id="f-po", name="PO_4711.pdf", mime_type="application/pdf"), payload=b"%PDF")
    up = HubSpotFileUploader(client, lister=lister, cache_dir=tmp_path / "cache", ledger=ledger, sleep_fn=lambda s: None)

    out = up.run(_rows(), upload_live=True, attach_live=True)
    assert out[0]["status"] == STATUS_ATTACHED
    kinds = [c[0] for c in client.calls]
    assert kinds == ["upload", "note", "assoc"]
    assert ("assoc", "note", out[0]["hs_note_id"], "company", "1001") in client.calls
    assert (tmp_path / "cache" / "f-po_PO_4711.pdf").read_bytes() == b"%PDF"

    # second run: ledger short-circuits both phases, zero new REST calls
    n_calls = len(client.calls)
    out2 = up.run(_rows(), upload_live=True, attach_live=True)
    assert out2[0]["status"] == STATUS_ATTACHED and len(client.calls) == n_calls
    assert ledger.load_attached_rows()[0]["legacy_library_id"] == "3020QT-aaaa"


def test_upload_phase_reports_missing_source(tmp_path: Path):
    client = FakeClient()
    up = HubSpotFileUploader(client, lister=None, sleep_fn=lambda s: None)
    row = LibraryFileRow(legacy_id="x", file_name="a.pdf", note_body="n", target_associations=[("company", "1")])
    out = up.upload_phase([row], live=True)
    assert out[0]["status"] == "failed" and out[0]["error"] == "file_not_found"
    assert out[0]["status"] != STATUS_UPLOADED
