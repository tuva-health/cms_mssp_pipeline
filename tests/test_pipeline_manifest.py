from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from mssp_pipeline.pipeline import run as pipeline_run
from mssp_pipeline.run_manifest import RunManifest, redact_url


def test_pipeline_writes_manifest(tmp_path):
    processing_cfg = SimpleNamespace(OUTPUT_TYPE="PARQUET", FILE_STORE="")
    manifest_dir = tmp_path / ".runs"

    with patch("mssp_pipeline.integration.downloader.Downloader"), patch(
        "mssp_pipeline.integration.state.StateManager"
    ), patch("mssp_pipeline.processing.run"):
        pipeline_run(
            aco="C1234",
            start_year=2025,
            download_dir=tmp_path / "downloads",
            processing_config=processing_cfg,
            run_id="run-1",
            manifest_dir=manifest_dir,
        )

    manifest = RunManifest.load("run-1", manifest_dir=manifest_dir)
    assert manifest.data["status"] == "completed"
    assert manifest.phase_status("download") == "completed"
    assert manifest.phase_status("process") == "completed"


def test_pipeline_resume_skips_completed_download(tmp_path):
    processing_cfg = SimpleNamespace(OUTPUT_TYPE="PARQUET", FILE_STORE="")
    manifest_dir = tmp_path / ".runs"

    prior = RunManifest("prior-run", manifest_dir=manifest_dir)
    prior.set_phase("download", "completed")
    prior.set_phase("process", "failed", error="boom")
    prior.finalize("failed")
    prior.save()

    with patch("mssp_pipeline.integration.downloader.Downloader") as downloader_cls, patch(
        "mssp_pipeline.integration.state.StateManager"
    ), patch("mssp_pipeline.processing.run"):
        pipeline_run(
            aco="C1234",
            start_year=2025,
            download_dir=tmp_path / "downloads",
            processing_config=processing_cfg,
            run_id="resumed",
            resume_run_id="prior-run",
            manifest_dir=manifest_dir,
        )

    downloader_cls.assert_not_called()
    resumed = RunManifest.load("resumed", manifest_dir=manifest_dir)
    assert resumed.phase_status("download") == "skipped"
    assert resumed.phase_status("process") == "completed"


def test_redact_url_strips_query_string():
    url = "https://acct.blob.core.windows.net/container?sv=2020&sig=SECRET"
    assert redact_url(url) == "https://acct.blob.core.windows.net/container?<redacted>"


def test_redact_url_strips_userinfo():
    assert redact_url("https://user:pass@host.example.com/path") == "https://***@host.example.com/path"


def test_redact_url_passes_through_clean_uris():
    assert redact_url("s3://bucket/prefix") == "s3://bucket/prefix"
    assert redact_url("/local/path") == "/local/path"
    assert redact_url(None) is None
    assert redact_url("") == ""


def test_pipeline_manifest_redacts_remote_store(tmp_path):
    processing_cfg = SimpleNamespace(OUTPUT_TYPE="PARQUET", FILE_STORE="")
    manifest_dir = tmp_path / ".runs"
    sas_url = "https://acct.blob.core.windows.net/c?sv=2020&sig=SECRET"

    with patch("mssp_pipeline.integration.downloader.Downloader"), patch(
        "mssp_pipeline.integration.state.StateManager"
    ), patch("mssp_pipeline.processing.run"):
        pipeline_run(
            aco="C1234",
            start_year=2025,
            download_dir=tmp_path / "downloads",
            processing_config=processing_cfg,
            run_id="redacted-run",
            manifest_dir=manifest_dir,
            remote_store=sas_url,
        )

    manifest = RunManifest.load("redacted-run", manifest_dir=manifest_dir)
    stored = manifest.data["params"]["remote_store"]
    assert "SECRET" not in stored
    assert stored.endswith("?<redacted>")
