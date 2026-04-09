"""Persistent state tracking for downloaded files."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .remote_store import (
    build_remote_store_client,
    default_remote_state_location,
    migrate_legacy_state_prefixes,
)


class StateManager:
    def __init__(
        self,
        state_file: Path,
        remote_store: str | None = None,
        azure_storage_connection_string: str = "",
        azure_storage_account: str = "",
        gcs_credentials_path: str = "",
        gcs_project_id: str = "",
    ):
        self._path = state_file
        self.remote_store = remote_store
        self.azure_storage_connection_string = azure_storage_connection_string
        self.azure_storage_account = azure_storage_account
        self.gcs_credentials_path = gcs_credentials_path
        self.gcs_project_id = gcs_project_id
        self._data: dict = self._load()

    def _load(self) -> dict:
        if self.remote_store:
            return self._load_from_remote()
        if self._path.exists():
            with open(self._path) as f:
                return migrate_legacy_state_prefixes(json.load(f))
        return migrate_legacy_state_prefixes({"last_run": None, "downloaded": {}})

    def _load_from_remote(self) -> dict:
        client = build_remote_store_client(self)
        location = default_remote_state_location(self.remote_store)
        try:
            return migrate_legacy_state_prefixes(json.loads(client.read_text(location)))
        except FileNotFoundError:
            return migrate_legacy_state_prefixes({"last_run": None, "downloaded": {}})

    def save(self) -> None:
        self._data["last_run"] = datetime.now(timezone.utc).isoformat()
        content = json.dumps(self._data, indent=2)
        if self.remote_store:
            client = build_remote_store_client(self)
            client.write_text(
                default_remote_state_location(self.remote_store),
                content,
                content_type="application/json",
            )
        else:
            with open(self._path, "w") as f:
                f.write(content)

    def reset(self) -> None:
        self._data = {"last_run": None, "downloaded": {}}

    def is_downloaded(self, aco: str, year: int, code: int, filename: str, last_updated: str) -> bool:
        """Return True if this exact file (by filename + last_updated) is already in state."""
        key = str(code)
        try:
            record = self._data["downloaded"][aco][str(year)][key][filename]
            return record["last_updated"] == last_updated
        except KeyError:
            return False

    def is_uploaded(self, aco: str, year: int, code: int, filename: str, last_updated: str) -> bool:
        """Return True if this file was successfully uploaded to S3."""
        key = str(code)
        try:
            record = self._data["downloaded"][aco][str(year)][key][filename]
            return record["last_updated"] == last_updated and "uploaded_at" in record
        except KeyError:
            return False

    def mark_downloaded(self, aco: str, year: int, code: int, filename: str, last_updated: str) -> None:
        d = self._data["downloaded"]
        d.setdefault(aco, {}).setdefault(str(year), {}).setdefault(str(code), {})[filename] = {
            "last_updated": last_updated,
            "downloaded_at": datetime.now(timezone.utc).isoformat(),
        }

    def mark_uploaded(
        self,
        aco: str,
        year: int,
        code: int,
        filename: str,
        last_updated: str,
        remote_prefix: str,
    ) -> None:
        """Record a successful remote upload. Sets both downloaded_at and uploaded_at."""
        self.mark_downloaded(aco, year, code, filename, last_updated)
        record = self._data["downloaded"][aco][str(year)][str(code)][filename]
        record["remote_prefix"] = remote_prefix
        record["s3_prefix"] = remote_prefix
        record["uploaded_at"] = datetime.now(timezone.utc).isoformat()

    @property
    def last_run(self) -> str | None:
        return self._data.get("last_run")
