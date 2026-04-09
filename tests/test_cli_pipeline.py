from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from unittest.mock import patch

from mssp_pipeline import __main__ as cli
from mssp_pipeline.pipeline import run as pipeline_run


def _install_fake_dotenv(monkeypatch):
    fake = types.ModuleType("dotenv")
    fake.load_dotenv = lambda: None
    monkeypatch.setitem(sys.modules, "dotenv", fake)
    sys.modules.pop("mssp_pipeline.config", None)


def test_download_main_uses_file_store_override(monkeypatch, tmp_path):
    _install_fake_dotenv(monkeypatch)
    monkeypatch.setattr(cli, "_check_config_file", lambda: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mssp-download",
            "--aco",
            "C1234",
            "--start-year",
            "2025",
            "--file-store",
            "gs://bucket/base",
        ],
    )

    with patch("mssp_pipeline.integration.downloader.Downloader") as downloader_cls, patch(
        "mssp_pipeline.integration.state.StateManager"
    ) as state_cls:
        cli.download_main()

    state_cls.assert_called_once()
    assert state_cls.call_args.kwargs["remote_store"] == "gs://bucket/base"
    config = downloader_cls.call_args.args[0]
    assert config.remote_store == "gs://bucket/base"


def test_download_main_legacy_s3_bucket_maps_to_remote_store(monkeypatch):
    _install_fake_dotenv(monkeypatch)
    monkeypatch.setattr(cli, "_check_config_file", lambda: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mssp-download",
            "--aco",
            "C1234",
            "--start-year",
            "2025",
            "--s3-bucket",
            "legacy-bucket",
        ],
    )

    with patch("mssp_pipeline.integration.downloader.Downloader"), patch(
        "mssp_pipeline.integration.state.StateManager"
    ) as state_cls:
        cli.download_main()

    assert state_cls.call_args.kwargs["remote_store"] == "s3://legacy-bucket"


def test_pipeline_run_passes_remote_store_to_processing(tmp_path):
    sys.modules.pop("mssp_pipeline.config", None)
    processing_cfg = SimpleNamespace(OUTPUT_TYPE="PARQUET", FILE_STORE="")

    fake = types.ModuleType("dotenv")
    fake.load_dotenv = lambda: None
    with patch.dict(sys.modules, {"dotenv": fake}), patch("mssp_pipeline.integration.downloader.Downloader"), patch(
        "mssp_pipeline.integration.state.StateManager"
    ), patch("mssp_pipeline.processing.run") as process_run:
        pipeline_run(
            aco="C1234",
            start_year=2025,
            download_dir=tmp_path / "downloads",
            remote_store="az://container/base",
            processing_config=processing_cfg,
        )

    assert process_run.call_args.args[0].FILE_STORE == "az://container/base"
