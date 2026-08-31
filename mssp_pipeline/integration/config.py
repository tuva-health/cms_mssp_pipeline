from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from mssp_pipeline.integration.remote_store import (
    parse_remote_store,
    validate_source_identifier,
)


@dataclass
class Config:
    aco: str
    start_year: int
    output_dir: Path
    state_file: Path
    cli_path: Path = field(default_factory=lambda: Path("./acoms-cli"))
    # Directory the CLI downloads zip files into (must contain config.txt).
    # Defaults to cwd (project root). Override in tests to keep them isolated.
    staging_dir: Path = field(default_factory=lambda: Path("."))
    # When set, extracted files are uploaded to this remote store and local copies deleted.
    remote_store: str | None = None
    azure_storage_connection_string: str = ""
    azure_storage_account: str = ""
    gcs_credentials_path: str = ""
    gcs_project_id: str = ""

    def __post_init__(self) -> None:
        # The ACO id is spliced into local paths and remote prefixes during
        # download, so validate it here — the ingest half of the invariant that
        # every path-spliced value is charset-validated on both ingest and read.
        validate_source_identifier(self.aco, field_name="ACO id")
        # A remote destination, when set, must be a supported, well-formed remote
        # URI before any upload composes keys against it. remote_store is only
        # ever a remote location (the local staging area is output_dir), so an
        # unrecognized or malformed value fails closed here.
        if self.remote_store:
            parse_remote_store(self.remote_store)

    @property
    def current_year(self) -> int:
        from datetime import date
        return date.today().year

    @property
    def years(self) -> list[int]:
        return list(range(self.start_year, self.current_year + 1))
