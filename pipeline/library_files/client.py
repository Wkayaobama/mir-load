"""Thin HubSpot REST client — ported from ic-load (no retries here; the
uploader adds them). Surface limited to what pass 1 needs: company
search/create, file upload, note create/delete, v4 default association."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Iterable, Optional

import requests

from .config import Settings


class HubSpotClient:
    def __init__(
        self,
        token: str,
        base_url: str = "https://api.hubapi.com",
        session: Optional[requests.Session] = None,
        timeout_s: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self._session = session or requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {token}"})

    @classmethod
    def from_settings(cls, settings: Settings) -> "HubSpotClient":
        return cls(token=settings.require_hubspot_token(), base_url=settings.api_base_url)

    # -- CRM: companies ------------------------------------------------------

    def search_companies_by_name(self, name: str, *, limit: int = 5) -> list[dict]:
        """POST /crm/v3/objects/companies/search — exact name match."""
        url = f"{self.base_url}/crm/v3/objects/companies/search"
        payload = {
            "filterGroups": [{"filters": [{"propertyName": "name", "operator": "EQ", "value": name}]}],
            "properties": ["name", "domain"],
            "limit": limit,
        }
        resp = self._session.post(url, json=payload, timeout=self.timeout_s)
        resp.raise_for_status()
        return resp.json().get("results", [])

    def create_company(self, *, name: str, **extra_properties: str) -> dict:
        url = f"{self.base_url}/crm/v3/objects/companies"
        payload = {"properties": {"name": name, **extra_properties}}
        resp = self._session.post(url, json=payload, timeout=self.timeout_s)
        resp.raise_for_status()
        return resp.json()

    # -- CRM: notes ----------------------------------------------------------

    def create_note(
        self,
        *,
        hs_note_body: str,
        hs_attachment_ids: Iterable[str],
        hs_timestamp_ms: Optional[int] = None,
        extra_properties: Optional[dict] = None,
    ) -> dict:
        ts = hs_timestamp_ms if hs_timestamp_ms is not None else int(time.time() * 1000)
        properties = {
            "hs_note_body": hs_note_body,
            "hs_attachment_ids": ";".join(str(i) for i in hs_attachment_ids),
            "hs_timestamp": str(ts),
        }
        if extra_properties:
            properties.update(extra_properties)
        url = f"{self.base_url}/crm/v3/objects/notes"
        resp = self._session.post(url, json={"properties": properties}, timeout=self.timeout_s)
        resp.raise_for_status()
        return resp.json()

    def delete_note(self, note_id: str) -> None:
        url = f"{self.base_url}/crm/v3/objects/notes/{note_id}"
        resp = self._session.delete(url, timeout=self.timeout_s)
        resp.raise_for_status()

    # -- Files API -----------------------------------------------------------

    def upload_file(
        self,
        path: Path,
        *,
        folder_path: str = "/mrload_library",
        access: str = "PRIVATE",
        overwrite: bool = False,
        file_name: Optional[str] = None,
    ) -> dict:
        url = f"{self.base_url}/files/v3/files"
        options = {"access": access, "overwrite": overwrite}
        with path.open("rb") as fp:
            files = {"file": (file_name or path.name, fp)}
            data = {"options": json.dumps(options), "folderPath": folder_path}
            resp = self._session.post(url, files=files, data=data, timeout=self.timeout_s)
        resp.raise_for_status()
        return resp.json()

    def delete_file(self, file_id: str) -> None:
        url = f"{self.base_url}/files/v3/files/{file_id}"
        resp = self._session.delete(url, timeout=self.timeout_s)
        resp.raise_for_status()

    # -- v4 default associations ---------------------------------------------

    def associate_default(
        self, from_object_type: str, from_object_id: str, to_object_type: str, to_object_id: str
    ) -> dict:
        """PUT /crm/v4/objects/{from}/{id}/associations/default/{to}/{id} (singular types)."""
        url = (
            f"{self.base_url}/crm/v4/objects/{from_object_type}/{from_object_id}"
            f"/associations/default/{to_object_type}/{to_object_id}"
        )
        resp = self._session.put(url, timeout=self.timeout_s)
        resp.raise_for_status()
        return resp.json() if resp.content else {}
