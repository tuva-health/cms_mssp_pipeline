from dataclasses import dataclass, field
from pathlib import Path


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
    # When set, extracted files are uploaded to this S3 bucket and local copies deleted.
    s3_bucket: str | None = None

    @property
    def current_year(self) -> int:
        from datetime import date
        return date.today().year

    @property
    def years(self) -> list[int]:
        return list(range(self.start_year, self.current_year + 1))
