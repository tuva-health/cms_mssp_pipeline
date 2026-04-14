from __future__ import annotations

import json
import sys
import types
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from mssp_pipeline import __main__ as cli
from mssp_pipeline.pipeline import run as pipeline_run


def _install_fake_dotenv(monkeypatch):
    fake = types.ModuleType("dotenv")
    fake.load_dotenv = lambda: None
    monkeypatch.setitem(sys.modules, "dotenv", fake)
    sys.modules.pop("mssp_pipeline.config", None)
    pkg = sys.modules.get("mssp_pipeline")
    if pkg is not None and hasattr(pkg, "config"):
        delattr(pkg, "config")


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


def test_pipeline_main_forwards_cleanup_flag(monkeypatch):
    _install_fake_dotenv(monkeypatch)
    monkeypatch.setattr(cli, "_check_config_file", lambda: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mssp-pipeline",
            "--aco",
            "C1234",
            "--start-year",
            "2025",
            "--cleanup-download-dir",
        ],
    )

    with patch("mssp_pipeline.pipeline.run") as pipeline_run_mock:
        cli.pipeline_main()

    assert pipeline_run_mock.call_args.kwargs["cleanup_download_dir"] is True


def test_validate_main_process_target_success(monkeypatch, tmp_path):
    _install_fake_dotenv(monkeypatch)
    monkeypatch.setenv("MSSP_ACO_ID", "C1234")
    monkeypatch.setenv("MSSP_FILE_STORE", str(tmp_path / "downloads"))
    monkeypatch.setenv("MSSP_OUTPUT_TYPE", "PARQUET")
    monkeypatch.setenv("MSSP_OUTPUT_LOCATION", str(tmp_path / "out"))
    sys.modules.pop("mssp_pipeline.config", None)

    monkeypatch.setattr(sys, "argv", ["mssp-validate", "--target", "process"])
    cli.validate_main()


def test_validate_main_process_target_fails_missing_aco(monkeypatch, tmp_path):
    _install_fake_dotenv(monkeypatch)
    monkeypatch.delenv("MSSP_ACO_ID", raising=False)
    monkeypatch.setenv("MSSP_FILE_STORE", str(tmp_path / "downloads"))
    monkeypatch.setenv("MSSP_OUTPUT_TYPE", "PARQUET")
    monkeypatch.setenv("MSSP_OUTPUT_LOCATION", str(tmp_path / "out"))
    sys.modules.pop("mssp_pipeline.config", None)

    monkeypatch.setattr(sys, "argv", ["mssp-validate", "--target", "process"])
    with pytest.raises(SystemExit):
        cli.validate_main()


def test_validate_main_json_output(monkeypatch, tmp_path, capsys):
    _install_fake_dotenv(monkeypatch)
    monkeypatch.setenv("MSSP_ACO_ID", "C1234")
    monkeypatch.setenv("MSSP_FILE_STORE", str(tmp_path / "downloads"))
    monkeypatch.setenv("MSSP_OUTPUT_TYPE", "PARQUET")
    monkeypatch.setenv("MSSP_OUTPUT_LOCATION", str(tmp_path / "out"))
    sys.modules.pop("mssp_pipeline.config", None)

    monkeypatch.setattr(sys, "argv", ["mssp-validate", "--target", "process", "--format", "json"])
    cli.validate_main()
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["target"] == "process"
    assert payload["strict"] is False
    assert payload["warnings"] == []
    assert payload["errors"] == []


def test_validate_main_strict_fails_on_warning(monkeypatch, tmp_path):
    _install_fake_dotenv(monkeypatch)
    monkeypatch.setenv("MSSP_ACO_ID", "C1234")
    monkeypatch.setenv("MSSP_FILE_STORE", "downloads")  # relative path -> warning
    monkeypatch.setenv("MSSP_OUTPUT_TYPE", "PARQUET")
    monkeypatch.setenv("MSSP_OUTPUT_LOCATION", str(tmp_path / "out"))
    sys.modules.pop("mssp_pipeline.config", None)

    monkeypatch.setattr(sys, "argv", ["mssp-validate", "--target", "process", "--strict"])
    with pytest.raises(SystemExit):
        cli.validate_main()
