"""Unified CLI entry point for mssp-download, mssp-process, and mssp-pipeline."""

from __future__ import annotations

import argparse
import json
import os
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
    env_path = os.environ.get("MSSP_CLI_PATH")
    if env_path:
        return Path(env_path).resolve()
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
    parser.add_argument("--run-id", help="Optional run id used for manifest naming")
    parser.add_argument("--resume-run-id", help="Resume from a prior run manifest by skipping completed phases")
    parser.add_argument("--resume-latest", action="store_true", help="Resume from the most recent run manifest")
    parser.add_argument("--manifest-dir", default=".runs", help="Directory for run manifests")
    args = parser.parse_args()

    cli_path = _resolve_cli_path(args.cli_path)

    if args.configure:
        from mssp_pipeline.integration.cli import run_configure

        print("Running acoms-cli configuration...")
        run_configure(cli_path)
        print("Configuration complete. config.txt saved.")
        return

    from mssp_pipeline import config as root_cfg

    if args.resume_latest and args.resume_run_id:
        print("Use either --resume-run-id or --resume-latest, not both.")
        raise SystemExit(1)

    resume_run_id = args.resume_run_id
    if args.resume_latest:
        from mssp_pipeline.run_manifest import latest_run_id

        resume_run_id = latest_run_id(args.manifest_dir)
        if not resume_run_id:
            print(f"No run manifests found in {args.manifest_dir}.")
            raise SystemExit(1)
        print(f"Resuming from latest run id: {resume_run_id}")

    effective_aco = args.aco or root_cfg.ACO_ID
    effective_start_year = args.start_year if args.start_year is not None else root_cfg.START_YEAR
    effective_mode = args.mode or root_cfg.DOWNLOAD_MODE
    effective_output_type = args.output_type or root_cfg.OUTPUT_TYPE

    if not args.skip_download:
        _check_config_file()
    if (not args.skip_download or not args.skip_process) and not effective_aco:
        print("ACO ID is required. Set --aco or MSSP_ACO_ID.")
        raise SystemExit(1)

    from mssp_pipeline.processing.config_defs import validate_config

    processing_config = None
    if not args.skip_process:
        processing_config = root_cfg.runtime_config(
            OUTPUT_TYPE=effective_output_type,
            FULL_REFRESH=True if args.full_refresh else root_cfg.FULL_REFRESH,
        )

        try:
            validate_config(processing_config)
        except ValueError as exc:
            print(f"Configuration error: {exc}")
            raise SystemExit(1)

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
            run_id=args.run_id,
            resume_run_id=resume_run_id,
            manifest_dir=args.manifest_dir,
            processing_config=processing_config,
        )
    except Exception as exc:
        print(str(exc))
        raise SystemExit(1)


def runs_main() -> None:
    parser = argparse.ArgumentParser(description="List or inspect pipeline run manifests.")
    parser.add_argument("--manifest-dir", default=".runs", help="Directory containing run manifests")
    parser.add_argument("--limit", type=int, default=20, help="Max runs to list (newest first)")
    parser.add_argument("--run-id", help="Show a single run manifest by id")
    parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format")
    args = parser.parse_args()

    from mssp_pipeline.run_manifest import RunManifest

    manifest_dir = Path(args.manifest_dir)
    if args.run_id:
        path = manifest_dir / f"{args.run_id}.json"
        if not path.exists():
            print(f"Run manifest not found: {path}")
            raise SystemExit(1)
        data = RunManifest.load(args.run_id, manifest_dir=manifest_dir).data
        if args.format == "json":
            print(json.dumps(data, indent=2))
        else:
            phases = data.get("phases", {})
            print(f"run_id: {data.get('run_id')}")
            print(f"status: {data.get('status')}")
            print(f"started_at: {data.get('started_at')}")
            print(f"ended_at: {data.get('ended_at')}")
            print(f"download: {phases.get('download', {}).get('status', 'n/a')}")
            print(f"process: {phases.get('process', {}).get('status', 'n/a')}")
            print(f"events: {len(data.get('events', []))}")
        return

    if not manifest_dir.exists():
        manifests: list[Path] = []
    else:
        manifests = sorted(
            [p for p in manifest_dir.glob("*.json") if p.is_file()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[: max(args.limit, 0)]

    rows = []
    for path in manifests:
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        phases = data.get("phases", {})
        rows.append(
            {
                "run_id": data.get("run_id", path.stem),
                "status": data.get("status", "unknown"),
                "started_at": data.get("started_at"),
                "ended_at": data.get("ended_at"),
                "download": phases.get("download", {}).get("status", "n/a"),
                "process": phases.get("process", {}).get("status", "n/a"),
                "events": len(data.get("events", [])),
            }
        )

    if args.format == "json":
        print(json.dumps({"manifest_dir": str(manifest_dir), "runs": rows}, indent=2))
        return

    if not rows:
        print(f"No run manifests found in {manifest_dir}")
        return

    for row in rows:
        print(
            f"{row['run_id']} status={row['status']} "
            f"download={row['download']} process={row['process']} events={row['events']} "
            f"started={row['started_at']}"
        )


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
    parser.add_argument("--live", action="store_true", help="Perform live filesystem/credential checks where possible")
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
            if args.live and not cli_path.is_file():
                errors.append(f"acoms-cli path is not a file: {cli_path}")

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

        if args.live and not errors:
            from mssp_pipeline.integration.remote_store import (
                build_remote_store_client,
                default_remote_state_location,
                is_remote_store,
            )

            if cfg.OUTPUT_TYPE in {"PARQUET", "DUCKDB"}:
                from pathlib import Path as _Path

                out_parent = _Path(cfg.OUTPUT_LOCATION).expanduser().parent
                try:
                    out_parent.mkdir(parents=True, exist_ok=True)
                    infos.append(f"Output path writable: {out_parent}")
                except Exception as exc:
                    errors.append(f"Output path not writable ({out_parent}): {exc}")

            if cfg.OUTPUT_TYPE in {"SNOWFLAKE", "DATABRICKS", "BIGQUERY", "REDSHIFT", "FABRIC"}:
                from pathlib import Path as _Path

                temp_dir = _Path(cfg.TEMP_LOCATION).expanduser()
                try:
                    temp_dir.mkdir(parents=True, exist_ok=True)
                    probe = temp_dir / ".validate_write_probe"
                    probe.write_text("ok")
                    probe.unlink()
                    infos.append(f"Staging path writable: {temp_dir}")
                except Exception as exc:
                    errors.append(f"Staging path not writable ({temp_dir}): {exc}")

            if is_remote_store(cfg.FILE_STORE):
                probe_cfg = root_cfg.runtime_config()
                probe_cfg.remote_store = cfg.FILE_STORE
                try:
                    client = build_remote_store_client(probe_cfg)
                    location = default_remote_state_location(cfg.FILE_STORE)
                    try:
                        client.read_text(location)
                    except FileNotFoundError:
                        pass
                    infos.append(f"Remote store credentials usable: {cfg.FILE_STORE}")
                except Exception as exc:
                    errors.append(f"Remote store credential check failed ({cfg.FILE_STORE}): {exc}")

    if args.format == "json":
        payload = {
            "target": args.target,
            "strict": args.strict,
            "live": args.live,
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
    # Allow running as `python -m mssp_pipeline download|process|pipeline|validate|runs`
    if len(sys.argv) < 2 or sys.argv[1] not in ("download", "process", "pipeline", "validate", "runs"):
        print("Usage: python -m mssp_pipeline <download|process|pipeline|validate|runs> [args...]")
        raise SystemExit(1)
    command = sys.argv.pop(1)
    {
        "download": download_main,
        "process": process_main,
        "pipeline": pipeline_main,
        "validate": validate_main,
        "runs": runs_main,
    }[command]()
