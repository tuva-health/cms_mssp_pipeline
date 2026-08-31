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


def test_resume_chain_does_not_repeat_a_phase_an_earlier_run_performed(tmp_path):
    """Resuming a resumed run must still skip the phase the first run did.

    A run that skips download because it resumed records "skipped", not
    "completed". Resuming *that* run therefore has to look past the status to
    the run the work is attributed to, or the third run downloads everything
    again.
    """
    processing_cfg = SimpleNamespace(OUTPUT_TYPE="PARQUET", FILE_STORE="")
    manifest_dir = tmp_path / ".runs"

    first = RunManifest("run-1", manifest_dir=manifest_dir)
    first.set_phase("download", "completed")
    first.set_phase("process", "failed", error="boom")
    first.finalize("failed")
    first.save()

    with patch("mssp_pipeline.integration.downloader.Downloader"), patch(
        "mssp_pipeline.integration.state.StateManager"
    ), patch("mssp_pipeline.processing.run", side_effect=RuntimeError("boom again")):
        try:
            pipeline_run(
                aco="C1234",
                start_year=2025,
                download_dir=tmp_path / "downloads",
                processing_config=processing_cfg,
                run_id="run-2",
                resume_run_id="run-1",
                manifest_dir=manifest_dir,
            )
        except RuntimeError:
            pass

    second = RunManifest.load("run-2", manifest_dir=manifest_dir)
    assert second.phase_status("download") == "skipped"
    assert second.phase_details("download")["satisfied_by"] == "run-1"

    with patch("mssp_pipeline.integration.downloader.Downloader") as downloader_cls, patch(
        "mssp_pipeline.integration.state.StateManager"
    ), patch("mssp_pipeline.processing.run"):
        pipeline_run(
            aco="C1234",
            start_year=2025,
            download_dir=tmp_path / "downloads",
            processing_config=processing_cfg,
            run_id="run-3",
            resume_run_id="run-2",
            manifest_dir=manifest_dir,
        )

    downloader_cls.assert_not_called()
    third = RunManifest.load("run-3", manifest_dir=manifest_dir)
    assert third.phase_status("download") == "skipped"
    assert third.phase_details("download")["satisfied_by"] == "run-1"


def test_resume_does_not_inherit_an_operator_requested_skip(tmp_path):
    """--skip-download is an instruction, not evidence the download happened.

    The prior run skipped download because it was told to, so a resume of it
    must still download rather than assume the files are present.
    """
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
            run_id="told-to-skip",
            skip_download=True,
            manifest_dir=manifest_dir,
        )

    prior = RunManifest.load("told-to-skip", manifest_dir=manifest_dir)
    assert prior.phase_status("download") == "skipped"
    assert "satisfied_by" not in prior.phase_details("download")

    with patch("mssp_pipeline.integration.downloader.Downloader") as downloader_cls, patch(
        "mssp_pipeline.integration.state.StateManager"
    ), patch("mssp_pipeline.processing.run"):
        pipeline_run(
            aco="C1234",
            start_year=2025,
            download_dir=tmp_path / "downloads",
            processing_config=processing_cfg,
            run_id="after-skip",
            resume_run_id="told-to-skip",
            manifest_dir=manifest_dir,
        )

    downloader_cls.assert_called_once()


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
        # skip_download keeps this focused on manifest redaction: the run records
        # (and redacts) the remote_store param regardless of the download phase.
        # An https SAS URL is not a supported remote store — the ingest side now
        # fails it closed — so exercising redaction must not route it through the
        # download Config.
        pipeline_run(
            aco="C1234",
            start_year=2025,
            download_dir=tmp_path / "downloads",
            processing_config=processing_cfg,
            run_id="redacted-run",
            manifest_dir=manifest_dir,
            remote_store=sas_url,
            skip_download=True,
        )

    manifest = RunManifest.load("redacted-run", manifest_dir=manifest_dir)
    stored = manifest.data["params"]["remote_store"]
    assert "SECRET" not in stored
    assert stored.endswith("?<redacted>")
