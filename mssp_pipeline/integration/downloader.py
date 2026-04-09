"""Orchestrates the view → compare → download → extract → cleanup loop."""

import zipfile
from pathlib import Path

from .cli import run_list, run_view, run_download
from .config import Config
from .parser import FileEntry, parse_list, parse_view
from .remote_store import build_remote_uploader
from .state import StateManager


class Downloader:
    def __init__(self, config: Config, state: StateManager):
        self.config = config
        self.state = state
        self._uploader = build_remote_uploader(config)

    def run(self) -> None:
        cfg = self.config
        for year in cfg.years:
            print(f"[{cfg.aco}] Checking year {year}...")
            list_output = run_list(cfg.cli_path, cfg.aco, year)
            codes = parse_list(list_output)
            if not codes:
                print(f"  No file types found for {year}.")
                continue
            for code in codes:
                self._process(year, code)

    def _process(self, year: int, code: int) -> None:
        cfg = self.config
        view_output = run_view(cfg.cli_path, cfg.aco, year, code)
        entries = parse_view(view_output)
        if not entries:
            print(f"  [{year}] code {code}: no files found in --view output, skipping.")
            return

        if self._uploader:
            to_download = [
                e for e in entries
                if not self.state.is_uploaded(cfg.aco, year, code, e.filename, e.last_updated)
            ]
        else:
            to_download = [
                e for e in entries
                if not self.state.is_downloaded(cfg.aco, year, code, e.filename, e.last_updated)
            ]

        if not to_download:
            print(f"  [{year}] code {code}: all {len(entries)} file(s) up to date, skipping.")
            return

        print(f"  [{year}] code {code}: {len(to_download)} new/updated file(s) to download.")

        # Use the creation date of the oldest new file as the --createdAfter cutoff.
        # If any filename has an unrecognised date format, skip the filter and
        # download everything for this code/year (safe — state prevents re-extraction).
        try:
            oldest_date = min(e.creation_date() for e in to_download)
            created_after = oldest_date.strftime("%Y-%m-%d")
        except ValueError as exc:
            print(f"  Warning: could not parse creation date ({exc}); downloading without --createdAfter filter.")
            created_after = None

        output_dir = cfg.output_dir / cfg.aco / str(year) / str(code)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Snapshot existing files in staging_dir (project root) before download
        staging_dir = cfg.staging_dir
        before_files = set(f for f in staging_dir.glob("*") if f.is_file())

        run_download(cfg.cli_path, cfg.aco, year, code, created_after=created_after)

        # Move newly downloaded files from staging_dir to output_dir
        new_files = set(f for f in staging_dir.glob("*") if f.is_file()) - before_files
        for file_path in new_files:
            file_path.rename(output_dir / file_path.name)

        self._extract_and_cleanup(output_dir)

        if self._uploader:
            remote_prefix = f"{cfg.aco}/{year}/{code}"
            self._uploader.upload_and_delete(output_dir, remote_prefix)
            for entry in to_download:
                self.state.mark_uploaded(cfg.aco, year, code, entry.filename, entry.last_updated, remote_prefix)
        else:
            for entry in to_download:
                self.state.mark_downloaded(cfg.aco, year, code, entry.filename, entry.last_updated)

        self.state.save()

    def _extract_and_cleanup(self, directory: Path) -> None:
        for zip_path in directory.glob("*.zip"):
            extract_dir = directory / zip_path.stem
            extract_dir.mkdir(exist_ok=True)
            print(f"    Extracting {zip_path.name} → {extract_dir.name}/")
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_dir)
            zip_path.unlink()
            print(f"    Deleted {zip_path.name}.")
