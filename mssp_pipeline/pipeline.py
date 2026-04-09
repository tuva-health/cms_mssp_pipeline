"""End-to-end pipeline: download files from CMS Datahub, then process them."""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace


def run(
    aco: str,
    start_year: int,
    download_dir: str | Path,
    *,
    download_mode: str = "incremental",
    cli_path: Path | None = None,
    state_file: Path | None = None,
    remote_store: str | None = None,
    reset_state: bool = False,
    skip_download: bool = False,
    skip_process: bool = False,
    processing_config=None,
) -> None:
    """Run the full download → process pipeline.

    Args:
        aco:               ACO identifier (e.g. 'C1234').
        start_year:        First performance year to download.
        download_dir:      Local directory where acoms-cli extracts files before they
                           are moved to the file store. Defaults to 'downloads/'.
                           Also used as FILE_STORE for the processing step when
                           running without a cloud destination.
        download_mode:     'incremental' (default) or 'full'.
        cli_path:          Path to the acoms-cli binary. Defaults to bin/acoms-cli
                           in the package root.
        state_file:        Path to the download state JSON. Defaults to state.json.
        remote_store:      Remote object store URI. When set, extracted files are
                           uploaded there and local copies deleted; state is also
                           stored in the same remote store.
        reset_state:       Wipe the download state before running (force re-download).
        skip_download:     Skip the download step (process already-present files).
        skip_process:      Skip the processing step (download only).
        processing_config: Config object for the processing step. If None, loads
                           from mssp_pipeline.processing.config and overrides
                           ACO_ID and FILE_STORE with the values passed here.
    """
    download_dir = Path(download_dir) if not isinstance(download_dir, str) else download_dir

    if cli_path is None:
        cli_path = Path(__file__).parent.parent / "bin" / "acoms-cli"
    if state_file is None:
        state_file = Path("state.json")

    # --- Download step ---
    if not skip_download:
        from mssp_pipeline.integration.config import Config
        from mssp_pipeline.integration.downloader import Downloader
        from mssp_pipeline.integration.state import StateManager
        from mssp_pipeline import config as root_cfg

        cfg = Config(
            aco=aco,
            start_year=start_year,
            output_dir=Path(download_dir),
            state_file=state_file,
            cli_path=Path(cli_path).resolve(),
            remote_store=remote_store,
            azure_storage_connection_string=root_cfg.AZURE_STORAGE_CONNECTION_STRING,
            azure_storage_account=root_cfg.AZURE_STORAGE_ACCOUNT,
            gcs_credentials_path=root_cfg.GCS_CREDENTIALS_PATH,
            gcs_project_id=root_cfg.GCS_PROJECT_ID,
        )

        state = StateManager(
            cfg.state_file,
            remote_store=remote_store,
            azure_storage_connection_string=root_cfg.AZURE_STORAGE_CONNECTION_STRING,
            azure_storage_account=root_cfg.AZURE_STORAGE_ACCOUNT,
            gcs_credentials_path=root_cfg.GCS_CREDENTIALS_PATH,
            gcs_project_id=root_cfg.GCS_PROJECT_ID,
        )

        if reset_state:
            print("Resetting download state — all files will be re-downloaded.")
            state.reset()

        print(f"[download] Mode: {download_mode} | ACO: {aco} | Years: {cfg.years[0]}–{cfg.years[-1]} | dir: {download_dir}")
        downloader = Downloader(cfg, state)
        downloader.run()
        print("[download] Done.")

    # --- Processing step ---
    if not skip_process:
        from mssp_pipeline.processing import run as process_run

        if processing_config is None:
            from mssp_pipeline.processing import config as _proc_cfg
            # Override the shared fields with the values passed to this function
            # so both steps always operate on the same ACO and directory.
            processing_config = SimpleNamespace(**{
                k: getattr(_proc_cfg, k) for k in dir(_proc_cfg) if not k.startswith("_")
            })
        processing_config.ACO_ID = aco
        processing_config.FILE_STORE = remote_store or str(download_dir)

        print(f"[process] ACO: {aco} | FILE_STORE: {processing_config.FILE_STORE} | OUTPUT_TYPE: {processing_config.OUTPUT_TYPE}")
        process_run(processing_config)
        print("[process] Done.")

    # Clean up the local download directory once both steps have completed.
    # raw_dir is always a local path — acoms-cli can only write to the local
    # filesystem, so even when files are also uploaded to S3, an intermediate
    # local copy was created.  The S3Uploader removes file contents after
    # upload, but leaves the (now-empty) directory tree behind; rmtree takes
    # care of that remainder.  We skip cleanup when either step was skipped:
    # if download was skipped the directory isn't ours to delete, and if
    # process was skipped the files may still be needed.
    if not skip_download and not skip_process:
        dl_path = Path(download_dir)
        if dl_path.exists():
            shutil.rmtree(dl_path)
            print(f"[cleanup] Removed local downloads from {dl_path}")
