"""The run manifest is a projection over the evidence core.

`tests/test_pipeline_manifest.py` pins the manifest's public API and on-disk
shape and must keep passing unchanged. These tests pin the *new* fact: the
manifest's phase view is derived from emitted evidence records, and those
records flow through an EvidenceSink when one is attached.
"""

from __future__ import annotations

from mssp_pipeline.evidence import project_run_summary
from mssp_pipeline.evidence.records import StageAttempted, StageReused, RunStarted
from mssp_pipeline.evidence.reuse import reuse_fingerprint
from mssp_pipeline.evidence.sink import InMemoryEvidenceSink
from mssp_pipeline.run_manifest import RunManifest


def test_completed_phase_emits_stage_attempted_through_the_sink(tmp_path):
    sink = InMemoryEvidenceSink()
    m = RunManifest("run-1", manifest_dir=tmp_path, sink=sink)
    m.set_phase("download", "running")
    m.set_phase("download", "completed")

    attempts = [r for r in sink.records() if isinstance(r, StageAttempted)]
    assert [(a.stage_id, a.outcome) for a in attempts] == [("download", "succeeded")]
    # The projected view still reports the byte-compatible status.
    assert m.phase_status("download") == "completed"


def test_failed_phase_emits_failed_execution_outcome(tmp_path):
    sink = InMemoryEvidenceSink()
    m = RunManifest("run-1", manifest_dir=tmp_path, sink=sink)
    m.set_phase("process", "failed", error="boom")

    (attempt,) = [r for r in sink.records() if isinstance(r, StageAttempted)]
    assert attempt.outcome == "failed"
    assert m.phase_status("process") == "failed"
    # The concrete error text stays in the manifest projection, never in the
    # neutral evidence record.
    assert m.phase_details("process") == {}  # no details set
    assert not hasattr(attempt, "error")


def test_resume_skip_emits_stage_reused_with_lineage(tmp_path):
    sink = InMemoryEvidenceSink()
    m = RunManifest("run-2", manifest_dir=tmp_path, sink=sink)
    m.set_phase("download", "skipped", details={"reason": "resume", "satisfied_by": "run-1"})

    (reused,) = [r for r in sink.records() if isinstance(r, StageReused)]
    assert reused.reused_from_run_id == "run-1"
    assert reused.fingerprint == reuse_fingerprint(stage_id="download", reused_from_run_id="run-1")
    assert m.phase_status("download") == "skipped"
    assert m.phase_satisfied_by("download") == "run-1"


def test_operator_skip_emits_no_reuse_record(tmp_path):
    sink = InMemoryEvidenceSink()
    m = RunManifest("run-1", manifest_dir=tmp_path, sink=sink)
    m.set_phase("download", "skipped", details={"reason": "skip_download=true"})

    assert [r for r in sink.records() if isinstance(r, StageReused)] == []
    assert m.phase_status("download") == "skipped"
    assert "satisfied_by" not in m.phase_details("download")


def test_manifest_view_is_derivable_from_its_evidence_records(tmp_path):
    m = RunManifest("run-1", manifest_dir=tmp_path)
    m.set_params(aco="C1234")
    m.set_phase("download", "completed")
    m.set_phase("process", "completed")

    records = m.evidence_records()
    assert any(isinstance(r, RunStarted) for r in records)
    summary = project_run_summary(records)
    assert summary.run_id == "run-1"
    assert summary.stage_execution == {"download": "succeeded", "process": "succeeded"}


def test_run_started_stores_param_digests_not_raw_values(tmp_path):
    m = RunManifest("run-1", manifest_dir=tmp_path)
    m.set_params(remote_store="s3://bucket/secret-prefix")
    (started,) = [r for r in m.evidence_records() if isinstance(r, RunStarted)]
    assert "s3://bucket/secret-prefix" not in str(started.param_digests)


# ---------------------------------------------------------------------------
# End-to-end: the pipeline writes a durable append-only evidence log
# ---------------------------------------------------------------------------

from types import SimpleNamespace  # noqa: E402
from unittest.mock import patch  # noqa: E402

from mssp_pipeline.evidence.sink import JsonlEvidenceSink  # noqa: E402
from mssp_pipeline.pipeline import run as pipeline_run  # noqa: E402


def test_pipeline_run_emits_evidence_log(tmp_path):
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

    records = JsonlEvidenceSink(manifest_dir / "run-1.evidence.jsonl").read()
    kinds = [type(r).__name__ for r in records]
    # The run root, then both stages recorded as execution outcomes.
    assert kinds[0] == "RunStarted"
    assert ("StageAttempted", "download", "succeeded") in [
        (type(r).__name__, r.stage_id, r.outcome) for r in records if isinstance(r, StageAttempted)
    ]
    summary = project_run_summary(records)
    assert summary.stage_execution == {"download": "succeeded", "process": "succeeded"}
