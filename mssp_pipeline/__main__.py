"""Unified CLI entry point for mssp-download, mssp-process, and mssp-pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
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


def _resolve_cli_path(cli_path_arg: str | None) -> Path:
    if cli_path_arg:
        return Path(cli_path_arg).resolve()
    return (Path(__file__).parent.parent / "bin" / "acoms-cli").resolve()


def _resolve_remote_store(args, root_cfg) -> str | None:
    if getattr(args, "s3_bucket", None):
        return f"s3://{args.s3_bucket}"
    return args.file_store or root_cfg.REMOTE_FILE_STORE or (
        f"s3://{root_cfg.S3_BUCKET}" if root_cfg.S3_BUCKET else None
    )


# ---------------------------------------------------------------------------
# mssp-download
# ---------------------------------------------------------------------------

def _add_download_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--aco", help="ACO identifier (e.g. C1234)")
    parser.add_argument("--start-year", type=int, help="First performance year")
    parser.add_argument("--mode", choices=["full", "incremental"], default=None)
    parser.add_argument("--output-dir", default="downloads", help="Root directory for extracted files")
    parser.add_argument("--state-file", default="state.json", help="Path to state tracking file")
    parser.add_argument("--cli-path", default=None, help="Path to acoms-cli binary (default: bin/acoms-cli)")
    parser.add_argument("--file-store", help="Remote file store override — s3://, az:// / abfss://, or gs://")
    parser.add_argument("--s3-bucket", help="S3 bucket for storing files and state")
    parser.add_argument("--configure", action="store_true", help="Run interactive acoms-cli configuration and exit")
    parser.add_argument("--reset-state", action="store_true", help="Wipe state before running")


def download_main() -> None:
    parser = argparse.ArgumentParser(description="Download MSSP ACO Datahub files via acoms-cli.")
    _add_download_args(parser)
    args = parser.parse_args()

    cli_path = _resolve_cli_path(args.cli_path)

    if args.configure:
        from mssp_pipeline.integration.cli import run_configure

        print("Running acoms-cli configuration...")
        run_configure(cli_path)
        print("Configuration complete. config.txt saved.")
        return

    _check_config_file()

    from mssp_pipeline import config as root_cfg
    from mssp_pipeline.integration.config import Config
    from mssp_pipeline.integration.downloader import Downloader
    from mssp_pipeline.integration.state import StateManager

    effective_aco = args.aco or root_cfg.ACO_ID
    effective_start_year = args.start_year if args.start_year is not None else root_cfg.START_YEAR
    effective_mode = args.mode or root_cfg.DOWNLOAD_MODE

    if not effective_aco:
        print("ACO ID is required. Set --aco or MSSP_ACO_ID.")
        raise SystemExit(1)

    remote_store = _resolve_remote_store(args, root_cfg)

    config = Config(
        aco=effective_aco,
        start_year=effective_start_year,
        output_dir=Path(args.output_dir),
        state_file=Path(args.state_file),
        cli_path=cli_path,
        remote_store=remote_store,
        azure_storage_connection_string=root_cfg.AZURE_STORAGE_CONNECTION_STRING,
        azure_storage_account=root_cfg.AZURE_STORAGE_ACCOUNT,
        gcs_credentials_path=root_cfg.GCS_CREDENTIALS_PATH,
        gcs_project_id=root_cfg.GCS_PROJECT_ID,
    )

    state = StateManager(
        config.state_file,
        remote_store=remote_store,
        azure_storage_connection_string=root_cfg.AZURE_STORAGE_CONNECTION_STRING,
        azure_storage_account=root_cfg.AZURE_STORAGE_ACCOUNT,
        gcs_credentials_path=root_cfg.GCS_CREDENTIALS_PATH,
        gcs_project_id=root_cfg.GCS_PROJECT_ID,
    )

    if args.reset_state or effective_mode == "full":
        print("Resetting state — all files will be re-downloaded.")
        state.reset()

    print(f"Mode: {effective_mode} | ACO: {config.aco} | Years: {config.years[0]}–{config.years[-1]}")

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

    from mssp_pipeline import config as root_cfg
    from mssp_pipeline.processing import run as process_run

    effective = root_cfg.runtime_config(
        ACO_ID=args.aco or root_cfg.ACO_ID,
        FILE_STORE=args.file_store or root_cfg.FILE_STORE,
        OUTPUT_TYPE=args.output_type or root_cfg.OUTPUT_TYPE,
        FULL_REFRESH=True if args.full_refresh else root_cfg.FULL_REFRESH,
    )

    try:
        process_run(effective)
    except Exception as exc:
        print(str(exc))
        raise SystemExit(1)


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
    parser.add_argument("--file-store", help="Remote file store override — s3://, az:// / abfss://, or gs://")
    parser.add_argument("--mode", choices=["full", "incremental"], default=None)
    parser.add_argument("--cli-path", default=None, help="Path to acoms-cli binary")
    parser.add_argument("--state-file", default="state.json")
    parser.add_argument("--s3-bucket", help="S3 bucket for storing downloaded files and state")
    parser.add_argument("--reset-state", action="store_true", help="Wipe download state before running")
    parser.add_argument("--skip-download", action="store_true", help="Skip download step")
    parser.add_argument("--skip-process", action="store_true", help="Skip processing step")
    parser.add_argument("--output-type", help="Processing output backend override")
    parser.add_argument("--full-refresh", action="store_true", help="Full refresh for the processing step")
    parser.add_argument("--configure", action="store_true", help="Run interactive acoms-cli configuration and exit")
    parser.add_argument("--cleanup-download-dir", action="store_true", help="Delete local download-dir after a successful run")
    args = parser.parse_args()

    cli_path = _resolve_cli_path(args.cli_path)

    if args.configure:
        from mssp_pipeline.integration.cli import run_configure

        print("Running acoms-cli configuration...")
        run_configure(cli_path)
        print("Configuration complete. config.txt saved.")
        return

    from mssp_pipeline import config as root_cfg

    effective_aco = args.aco or root_cfg.ACO_ID
    effective_start_year = args.start_year if args.start_year is not None else root_cfg.START_YEAR
    effective_mode = args.mode or root_cfg.DOWNLOAD_MODE

    if not args.skip_download:
        _check_config_file()
    if (not args.skip_download or not args.skip_process) and not effective_aco:
        print("ACO ID is required. Set --aco or MSSP_ACO_ID.")
        raise SystemExit(1)

    processing_config = root_cfg.runtime_config(
        OUTPUT_TYPE=args.output_type or root_cfg.OUTPUT_TYPE,
        FULL_REFRESH=True if args.full_refresh else root_cfg.FULL_REFRESH,
    )

    from mssp_pipeline.pipeline import run as pipeline_run

    try:
        pipeline_run(
            aco=effective_aco,
            start_year=effective_start_year,
            download_dir=args.download_dir,
            download_mode=effective_mode,
            cli_path=cli_path,
            state_file=Path(args.state_file),
            remote_store=_resolve_remote_store(args, root_cfg),
            reset_state=args.reset_state,
            skip_download=args.skip_download,
            skip_process=args.skip_process,
            cleanup_download_dir=args.cleanup_download_dir,
            processing_config=processing_config,
        )
    except Exception as exc:
        print(str(exc))
        raise SystemExit(1)


def validate_main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate configuration and local prerequisites without running the pipeline."
    )
    parser.add_argument(
        "--target",
        choices=["download", "process", "pipeline"],
        default="pipeline",
        help="Which phase prerequisites to validate",
    )
    parser.add_argument("--cli-path", default=None, help="Path to acoms-cli binary (download/pipeline target)")
    parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as failures")
    args = parser.parse_args()

    from mssp_pipeline import config as root_cfg
    from mssp_pipeline.processing.config_defs import validate_config

    errors: list[str] = []
    warnings: list[str] = []
    infos: list[str] = []

    if args.target in {"download", "pipeline"}:
        cli_path = _resolve_cli_path(args.cli_path)
        if not cli_path.exists():
            errors.append(f"acoms-cli not found: {cli_path}")
        else:
            infos.append(f"acoms-cli found: {cli_path}")

        if not CONFIG_FILE.exists():
            errors.append("config.txt missing (run: mssp-download --configure)")
        else:
            infos.append("config.txt found")

        if root_cfg.DOWNLOAD_MODE not in {"incremental", "full"}:
            errors.append(
                f"Invalid MSSP_DOWNLOAD_MODE={root_cfg.DOWNLOAD_MODE!r} (expected incremental|full)"
            )

        if not root_cfg.ACO_ID:
            errors.append("MSSP_ACO_ID is not set")

        if root_cfg.START_YEAR > date.today().year:
            warnings.append(
                f"MSSP_START_YEAR={root_cfg.START_YEAR} is in the future; download may return no files"
            )

    if args.target in {"process", "pipeline"}:
        cfg = root_cfg.runtime_config()
        if not cfg.ACO_ID:
            errors.append("MSSP_ACO_ID is not set")
        if not cfg.FILE_STORE:
            errors.append("MSSP_FILE_STORE is not set")
        elif not cfg.FILE_STORE.startswith(("/", "s3://", "az://", "azure://", "abfss://", "gs://")):
            warnings.append(
                f"MSSP_FILE_STORE={cfg.FILE_STORE!r} is a relative path; use an absolute path for scheduled/CI runs"
            )

        if cfg.OUTPUT_TYPE in {"SNOWFLAKE", "DATABRICKS", "BIGQUERY", "REDSHIFT", "FABRIC"} and cfg.TEMP_LOCATION == "./STAGED":
            warnings.append("MSSP_TEMP_LOCATION is using default './STAGED'; consider an explicit dedicated staging path")

        if root_cfg.S3_BUCKET and root_cfg.REMOTE_FILE_STORE and not root_cfg.REMOTE_FILE_STORE.startswith(f"s3://{root_cfg.S3_BUCKET}"):
            warnings.append("MSSP_S3_BUCKET and MSSP_FILE_STORE point to different remote stores; legacy bucket override may be confusing")

        try:
            validate_config(cfg)
            infos.append(f"Processing config valid for OUTPUT_TYPE={cfg.OUTPUT_TYPE}")
        except Exception as exc:
            errors.append(str(exc))

    if args.format == "json":
        payload = {
            "target": args.target,
            "strict": args.strict,
            "ok": not errors and (not args.strict or not warnings),
            "info": infos,
            "warnings": warnings,
            "errors": errors,
        }
        print(json.dumps(payload, indent=2))
        if errors or (args.strict and warnings):
            raise SystemExit(1)
        return

    if infos:
        for msg in infos:
            print(f"[ok] {msg}")

    if warnings:
        for msg in warnings:
            print(f"[warn] {msg}")

    if errors:
        for msg in errors:
            print(f"[error] {msg}")
        raise SystemExit(1)

    if args.strict and warnings:
        print("Strict mode failed due to warning(s).")
        raise SystemExit(1)

    print("Validation successful.")


if __name__ == "__main__":
    # Allow running as `python -m mssp_pipeline download|process|pipeline|validate`
    if len(sys.argv) < 2 or sys.argv[1] not in ("download", "process", "pipeline", "validate"):
        print("Usage: python -m mssp_pipeline <download|process|pipeline|validate> [args...]")
        raise SystemExit(1)
    command = sys.argv.pop(1)
    {
        "download": download_main,
        "process": process_main,
        "pipeline": pipeline_main,
        "validate": validate_main,
    }[command]()
