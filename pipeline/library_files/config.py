from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv_layers() -> None:
    """Two-stage env load mirroring ic-load: a shared ``.env.mrload`` at the
    codebase root, then a worktree-local ``.env`` that overrides it. Process
    env beats both. python-dotenv is optional."""
    try:
        from dotenv import find_dotenv, load_dotenv
    except ImportError:  # pragma: no cover - optional dependency
        return
    load_dotenv(find_dotenv(filename=".env.mrload", usecwd=True))
    load_dotenv(find_dotenv(usecwd=True), override=True)


@dataclass(frozen=True)
class Settings:
    hubspot_token: str | None
    hubspot_portal_id: str | None
    google_credentials: str | None      # service-account JSON path; None → ADC
    ledger_path: Path
    cache_dir: Path
    bq_project: str | None
    bq_dataset: str
    api_base_url: str = "https://api.hubapi.com"

    @classmethod
    def from_env(cls, *, token_var: str = "HUBSPOT_SANDBOX_TOKEN") -> "Settings":
        _load_dotenv_layers()
        return cls(
            hubspot_token=os.environ.get(token_var) or None,
            hubspot_portal_id=os.environ.get("HUBSPOT_SANDBOX_PORTAL_ID"),
            google_credentials=os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or None,
            ledger_path=Path(os.environ.get("MRLOAD_LEDGER_PATH", ".mrload/ledger.sqlite")),
            cache_dir=Path(os.environ.get("MRLOAD_CACHE_DIR", ".mrload/cache")),
            bq_project=os.environ.get("MRLOAD_BQ_PROJECT") or None,
            bq_dataset=os.environ.get("MRLOAD_BQ_DATASET", "mrload"),
            api_base_url=os.environ.get("MRLOAD_HUBSPOT_API_BASE", "https://api.hubapi.com"),
        )

    def require_hubspot_token(self, token_var: str = "HUBSPOT_SANDBOX_TOKEN") -> str:
        if not self.hubspot_token:
            raise RuntimeError(
                f"{token_var} is not set. Populate .env.mrload at the codebase root "
                f"or .env in this worktree."
            )
        return self.hubspot_token
