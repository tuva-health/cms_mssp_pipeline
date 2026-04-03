"""Unified CLI entry point for mssp-download, mssp-process, and mssp-pipeline."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

CONFIG_FILE = Path("config.txt")


def _check_config_file() -> None:
    if not CONFIG_FILE.exists():
        print(
            "No config.txt found. Run with --configure first to set up your API credentials.\n"
            "  mssp-download --configure"
        )
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# mssp-download
# ---------------------------------------------------------------------------

def _add_download_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--aco", help="ACO identifier (e.g. C1234)")
    parser.add_argument("--start-year", type=int, help="First performance year")
    parser.add_argument("--mode", choices=["full", "incremental"], default="incremental")
    parser.add_argument("--output-dir", default="downloads", help="Root directory for extracted files")
    parser.add_argument("--state-file", default="state.json", help="Path to state tracking file")
    parser.add_argument("--cli-path", default=None, help="Path to acoms-cli binary (default: bin/acoms-cli)")
    parser.add_argument("--s3-bucket", help="S3 bucket for storing files and state")
    parser.add_argument("--configure", action="store_true", help="Run interactive acoms-cli configuration and exit")
    parser.add_argument("--reset-state", action="store_true", help="Wipe state before running")


def download_main() -> None:
    parser = argparse.ArgumentParser(description="Download MSSP ACO Datahub files via acoms-cli.")
    _add_download_args(parser)
    args = parser.parse_args()

    if args.cli_path:
        cli_path = Path(args.cli_path).resolve()
    else:
        cli_path = (Path(__file__).parent.parent / "bin" / "acoms-cli").resolve()

    if args.configure:
        from mssp_pipeline.integration.cli import run_configure, CLIError
        print("Running acoms-cli configuration...")
        run_configure(cli_path)
        print("Configuration complete. config.txt saved.")
        return

    _check_config_file()

    if not args.aco or not args.start_year:
        print("--aco and --start-year are required when running in download mode.")
        raise SystemExit(1)

    from mssp_pipeline.integration.config import Config
    from mssp_pipeline.integration.downloader import Downloader
    from mssp_pipeline.integration.state import StateManager

    config = Config(
        aco=args.aco,
        start_year=args.start_year,
        output_dir=Path(args.output_dir),
        state_file=Path(args.state_file),
        cli_path=cli_path,
        s3_bucket=args.s3_bucket,
    )

    state = StateManager(config.state_file, s3_bucket=args.s3_bucket)

    if args.reset_state:
        print("Resetting state — all files will be re-downloaded.")
        state.reset()

    print(f"Mode: {args.mode} | ACO: {config.aco} | Years: {config.years[0]}–{config.years[-1]}")

    downloader = Downloader(config, state)
    downloader.run()
    print("Done.")


# ---------------------------------------------------------------------------
# mssp-process
# ---------------------------------------------------------------------------

def process_main() -> None:
    parser = argparse.ArgumentParser(description="Process downloaded MSSP files via DuckDB.")
    parser.add_argument("--aco", help="ACO identifier override (default: from config.py)")
    parser.add_argument("--file-store", help="File store override — local path, s3://, or az:// (default: from config.py)")
    parser.add_argument("--output-type", help="Output backend override (PARQUET, DUCKDB, SNOWFLAKE, ...)")
    parser.add_argument("--full-refresh", action="store_true", help="Drop and recreate all output tables")
    args = parser.parse_args()

    from mssp_pipeline.processing import run as process_run
    from mssp_pipeline.processing import config as cfg
    from types import SimpleNamespace

    # Build a config object, applying any CLI overrides
    effective = SimpleNamespace(**{k: getattr(cfg, k) for k in dir(cfg) if not k.startswith("_")})
    if args.aco:
        effective.ACO_ID = args.aco
    if args.file_store:
        effective.FILE_STORE = args.file_store
    if args.output_type:
        effective.OUTPUT_TYPE = args.output_type
    if args.full_refresh:
        effective.FULL_REFRESH = True

    process_run(effective)


# ---------------------------------------------------------------------------
# mssp-pipeline
# ---------------------------------------------------------------------------

def pipeline_main() -> None:
    parser = argparse.ArgumentParser(
        description="End-to-end pipeline: download MSSP files from CMS Datahub, then process them."
    )
    parser.add_argument("--aco", help="ACO identifier (e.g. C1234)")
    parser.add_argument("--start-year", type=int, help="First performance year")
    parser.add_argument("--download-dir", default="downloads", help="Local directory for downloaded/extracted files before they are moved to the file store")
    parser.add_argument("--mode", choices=["full", "incremental"], default="incremental")
    parser.add_argument("--cli-path", default=None, help="Path to acoms-cli binary")
    parser.add_argument("--state-file", default="state.json")
    parser.add_argument("--s3-bucket", help="S3 bucket for storing downloaded files and state")
    parser.add_argument("--reset-state", action="store_true", help="Wipe download state before running")
    parser.add_argument("--skip-download", action="store_true", help="Skip download step")
    parser.add_argument("--skip-process", action="store_true", help="Skip processing step")
    parser.add_argument("--output-type", help="Processing output backend override")
    parser.add_argument("--full-refresh", action="store_true", help="Full refresh for the processing step")
    parser.add_argument("--configure", action="store_true", help="Run interactive acoms-cli configuration and exit")
    args = parser.parse_args()

    if args.cli_path:
        cli_path = Path(args.cli_path).resolve()
    else:
        cli_path = (Path(__file__).parent.parent / "bin" / "acoms-cli").resolve()

    if args.configure:
        from mssp_pipeline.integration.cli import run_configure
        print("Running acoms-cli configuration...")
        run_configure(cli_path)
        print("Configuration complete. config.txt saved.")
        return

    if not args.skip_download:
        _check_config_file()
        if not args.aco or not args.start_year:
            print("--aco and --start-year are required.")
            raise SystemExit(1)

    processing_config = None
    if args.output_type or args.full_refresh:
        from mssp_pipeline.processing import config as cfg
        from types import SimpleNamespace
        processing_config = SimpleNamespace(**{k: getattr(cfg, k) for k in dir(cfg) if not k.startswith("_")})
        if args.output_type:
            processing_config.OUTPUT_TYPE = args.output_type
        if args.full_refresh:
            processing_config.FULL_REFRESH = True

    from mssp_pipeline.pipeline import run as pipeline_run
    pipeline_run(
        aco=args.aco,
        start_year=args.start_year,
        download_dir=args.download_dir,
        download_mode=args.mode,
        cli_path=cli_path,
        state_file=Path(args.state_file),
        s3_bucket=args.s3_bucket,
        reset_state=args.reset_state,
        skip_download=args.skip_download,
        skip_process=args.skip_process,
        processing_config=processing_config,
    )


if __name__ == "__main__":
    # Allow running as `python -m mssp_pipeline download|process|pipeline`
    if len(sys.argv) < 2 or sys.argv[1] not in ("download", "process", "pipeline"):
        print("Usage: python -m mssp_pipeline <download|process|pipeline> [args...]")
        raise SystemExit(1)
    command = sys.argv.pop(1)
    {"download": download_main, "process": process_main, "pipeline": pipeline_main}[command]()
