"""Unit seams for the append-only run-evidence core (mssp_pipeline/evidence).

These tests pin the client-neutral behavior of the evidence core: the seven
record types and their validators, the EvidenceSink interface and its
development JSONL sink, artifact identity, evidence validity, reuse
fingerprints, the named stage contract, and the summary projection. They never
assert commands or destinations — the core stores logical IDs and digests only.
"""

from __future__ import annotations

import hashlib

import pytest

from mssp_pipeline.evidence import identity


# ---------------------------------------------------------------------------
# Artifact identity / digest helpers
# ---------------------------------------------------------------------------


def test_digest_is_stable_hex_sha256():
    value = "performance-year-2025"
    expected = hashlib.sha256(value.encode("utf-8")).hexdigest()
    assert identity.digest(value) == expected
    # Stable across calls and independent of the caller.
    assert identity.digest(value) == identity.digest(value)


def test_digest_accepts_bytes_and_str_equivalently():
    assert identity.digest("abc") == identity.digest(b"abc")


def test_validate_logical_id_accepts_safe_ids():
    for good in ("download", "process", "stage.download", "aco_workbook-1"):
        assert identity.validate_logical_id(good) == good


def test_validate_logical_id_rejects_destinations_and_commands():
    # A logical id must never smuggle a destination (has "://") or a command
    # (has whitespace / shell separators). This is how the core enforces
    # "logical ids only, never concrete destinations or commands".
    for bad in ("s3://bucket/prefix", "mssp-process --aco C1234", "a/b", "", "  ", "a\tb"):
        with pytest.raises(ValueError):
            identity.validate_logical_id(bad)


def test_is_digest_recognizes_hex_sha256_only():
    assert identity.is_digest(identity.digest("x")) is True
    assert identity.is_digest("not-a-digest") is False
    assert identity.is_digest("abc123") is False  # too short


# ---------------------------------------------------------------------------
# The seven record types + validators
# ---------------------------------------------------------------------------

from mssp_pipeline.evidence import records as rec  # noqa: E402
from mssp_pipeline.evidence.records import validate_record  # noqa: E402


def test_the_seven_record_types_exist():
    # The append-only graph is made of exactly these seven neutral record types.
    assert rec.RECORD_TYPES == (
        "RunStarted",
        "StageAttempted",
        "StageReused",
        "PrerequisiteChecked",
        "StageBlocked",
        "MetricObserved",
        "ArtifactObserved",
    )


def test_run_started_round_trips_and_validates():
    r = rec.RunStarted(
        run_id="run-1",
        occurred_at="2026-01-01T00:00:00+00:00",
        param_digests={"aco": identity.digest("C1234")},
    )
    validate_record(r)
    assert r.record_type == "RunStarted"
    restored = rec.record_from_dict(r.to_dict())
    assert restored == r


def test_run_started_rejects_raw_param_values():
    # A param must be stored as a digest, never as its raw value — otherwise a
    # destination or secret could leak into evidence.
    r = rec.RunStarted(
        run_id="run-1",
        occurred_at="2026-01-01T00:00:00+00:00",
        param_digests={"file_store": "s3://bucket/prefix"},
    )
    with pytest.raises(ValueError):
        validate_record(r)


def test_stage_attempted_records_execution_outcome():
    r = rec.StageAttempted(
        run_id="run-1",
        occurred_at="2026-01-01T00:00:00+00:00",
        stage_id="download",
        outcome="succeeded",
    )
    validate_record(r)
    assert r.record_type == "StageAttempted"
    with pytest.raises(ValueError):
        validate_record(
            rec.StageAttempted(
                run_id="run-1",
                occurred_at="t",
                stage_id="download",
                outcome="maybe",  # not a valid execution outcome
            )
        )


def test_stage_attempted_rejects_command_shaped_stage_id():
    r = rec.StageAttempted(
        run_id="run-1",
        occurred_at="t",
        stage_id="mssp-process --aco C1234",
        outcome="succeeded",
    )
    with pytest.raises(ValueError):
        validate_record(r)


def test_stage_reused_carries_reuse_lineage_and_fingerprint():
    r = rec.StageReused(
        run_id="run-3",
        occurred_at="t",
        stage_id="download",
        reused_from_run_id="run-1",
        fingerprint=identity.digest("download|run-1"),
    )
    validate_record(r)
    assert r.reused_from_run_id == "run-1"
    with pytest.raises(ValueError):
        validate_record(
            rec.StageReused(
                run_id="run-3",
                occurred_at="t",
                stage_id="download",
                reused_from_run_id="run-1",
                fingerprint="not-a-digest",
            )
        )


def test_prerequisite_checked_is_opaque():
    r = rec.PrerequisiteChecked(
        run_id="run-1",
        occurred_at="t",
        prerequisite_id="bootstrap",
        observed_digest=identity.digest("true"),
        satisfied=True,
    )
    validate_record(r)
    # The observed value is opaque: only its digest is stored, never the path
    # or value it came from.
    with pytest.raises(ValueError):
        validate_record(
            rec.PrerequisiteChecked(
                run_id="run-1",
                occurred_at="t",
                prerequisite_id="bootstrap",
                observed_digest="/mssp/bootstrap_complete=true",
                satisfied=True,
            )
        )


def test_stage_blocked_names_the_blocking_prerequisite():
    r = rec.StageBlocked(
        run_id="run-1",
        occurred_at="t",
        stage_id="process",
        prerequisite_id="bootstrap",
        reason="unsatisfied_prerequisite",
    )
    validate_record(r)
    assert r.record_type == "StageBlocked"


def test_metric_observed_is_a_typed_metric():
    r = rec.MetricObserved(
        run_id="run-1",
        occurred_at="t",
        stage_id="process",
        metric_id="rows_written",
        value=42,
    )
    validate_record(r)
    assert r.value == 42
    with pytest.raises(ValueError):
        validate_record(
            rec.MetricObserved(
                run_id="run-1",
                occurred_at="t",
                stage_id="process",
                metric_id="rows_written",
                value="lots",  # metrics are typed integers
            )
        )


def test_artifact_observed_carries_intended_and_observed_digests():
    r = rec.ArtifactObserved(
        run_id="run-1",
        occurred_at="t",
        artifact_id="workbook_export",
        intended_digest=identity.digest("v1"),
        observed_digest=identity.digest("v1"),
    )
    validate_record(r)
    assert r.record_type == "ArtifactObserved"
    with pytest.raises(ValueError):
        validate_record(
            rec.ArtifactObserved(
                run_id="run-1",
                occurred_at="t",
                artifact_id="workbook_export",
                intended_digest="s3://bucket/workbook",  # a destination, not a digest
                observed_digest=identity.digest("v1"),
            )
        )


def test_record_from_dict_round_trips_every_type():
    samples = [
        rec.RunStarted(run_id="r", occurred_at="t", param_digests={}),
        rec.StageAttempted(run_id="r", occurred_at="t", stage_id="s", outcome="failed"),
        rec.StageReused(run_id="r", occurred_at="t", stage_id="s", reused_from_run_id="p", fingerprint=identity.digest("s|p")),
        rec.PrerequisiteChecked(run_id="r", occurred_at="t", prerequisite_id="p", observed_digest=identity.digest("v"), satisfied=False),
        rec.StageBlocked(run_id="r", occurred_at="t", stage_id="s", prerequisite_id="p", reason="unsatisfied_prerequisite"),
        rec.MetricObserved(run_id="r", occurred_at="t", stage_id="s", metric_id="m", value=1),
        rec.ArtifactObserved(run_id="r", occurred_at="t", artifact_id="a", intended_digest=identity.digest("i"), observed_digest=identity.digest("o")),
    ]
    for r in samples:
        assert rec.record_from_dict(r.to_dict()) == r


# ---------------------------------------------------------------------------
# EvidenceSink interface + development JSONL sink
# ---------------------------------------------------------------------------

from mssp_pipeline.evidence import sink as sink_mod  # noqa: E402


def _sample_records():
    return [
        rec.RunStarted(run_id="run-1", occurred_at="t0", param_digests={"aco": identity.digest("C1234")}),
        rec.StageAttempted(run_id="run-1", occurred_at="t1", stage_id="download", outcome="succeeded"),
        rec.MetricObserved(run_id="run-1", occurred_at="t2", stage_id="process", metric_id="rows_written", value=7),
    ]


def test_evidence_sink_is_an_interface():
    with pytest.raises(TypeError):
        sink_mod.EvidenceSink()  # abstract; cannot instantiate


def test_in_memory_sink_appends_in_order():
    sink = sink_mod.InMemoryEvidenceSink()
    for r in _sample_records():
        sink.append(r)
    assert list(sink.records()) == _sample_records()


def test_sink_validates_on_append():
    sink = sink_mod.InMemoryEvidenceSink()
    with pytest.raises(ValueError):
        sink.append(rec.StageAttempted(run_id="run-1", occurred_at="t", stage_id="download", outcome="bogus"))
    # A rejected record is never appended.
    assert list(sink.records()) == []


def test_jsonl_sink_is_append_only_and_reads_back(tmp_path):
    path = tmp_path / "run-1.evidence.jsonl"
    sink = sink_mod.JsonlEvidenceSink(path)
    for r in _sample_records():
        sink.append(r)

    # One JSON object per line, in append order.
    lines = path.read_text().splitlines()
    assert len(lines) == 3

    # Re-opening the same path appends, never truncates.
    sink2 = sink_mod.JsonlEvidenceSink(path)
    sink2.append(rec.StageAttempted(run_id="run-1", occurred_at="t3", stage_id="process", outcome="succeeded"))
    assert len(path.read_text().splitlines()) == 4

    # read() reconstructs the typed records in order.
    restored = sink_mod.JsonlEvidenceSink(path).read()
    assert restored == _sample_records() + [
        rec.StageAttempted(run_id="run-1", occurred_at="t3", stage_id="process", outcome="succeeded")
    ]


# ---------------------------------------------------------------------------
# Named stage contract for expected metric ids
# ---------------------------------------------------------------------------

from mssp_pipeline.evidence import contract as contract_mod  # noqa: E402


def _synthetic_contract():
    # A synthetic, non-client contract: the populated contract is A-class policy.
    return contract_mod.StageContract(
        {"download": {"files_downloaded"}, "process": {"rows_written", "tables_written"}}
    )


def test_stage_contract_reports_expected_metric_ids():
    c = _synthetic_contract()
    assert c.expected("process") == frozenset({"rows_written", "tables_written"})
    assert c.declares("process", "rows_written") is True
    assert c.declares("process", "unknown") is False
    # An unknown stage declares nothing.
    assert c.expected("nope") == frozenset()


def test_stage_contract_rejects_command_shaped_ids_at_construction():
    with pytest.raises(ValueError):
        contract_mod.StageContract({"process": {"rows written"}})
    with pytest.raises(ValueError):
        contract_mod.StageContract({"mssp-process --x": {"rows_written"}})


def test_validate_metric_rejects_undeclared_metric_id():
    c = _synthetic_contract()
    ok = rec.MetricObserved(run_id="r", occurred_at="t", stage_id="process", metric_id="rows_written", value=1)
    contract_mod.validate_metric(ok, c)  # declared → passes
    undeclared = rec.MetricObserved(run_id="r", occurred_at="t", stage_id="process", metric_id="surprise", value=1)
    with pytest.raises(ValueError):
        contract_mod.validate_metric(undeclared, c)


def test_missing_metrics_flags_declared_but_unobserved():
    c = _synthetic_contract()
    observed = {"rows_written"}
    assert contract_mod.missing_metrics("process", observed, c) == frozenset({"tables_written"})
    assert contract_mod.missing_metrics("process", {"rows_written", "tables_written"}, c) == frozenset()


# ---------------------------------------------------------------------------
# Evidence validity (separate from execution) + reuse fingerprints
# ---------------------------------------------------------------------------

from mssp_pipeline.evidence import validity as validity_mod  # noqa: E402
from mssp_pipeline.evidence import reuse as reuse_mod  # noqa: E402


def test_validity_is_valid_only_when_observed_matches_intended():
    valid = rec.ArtifactObserved(
        run_id="r", occurred_at="t", artifact_id="workbook",
        intended_digest=identity.digest("v1"), observed_digest=identity.digest("v1"),
    )
    invalid = rec.ArtifactObserved(
        run_id="r", occurred_at="t", artifact_id="workbook",
        intended_digest=identity.digest("v1"), observed_digest=identity.digest("v2"),
    )
    assert validity_mod.artifact_validity(valid) == "valid"
    assert validity_mod.artifact_validity(invalid) == "invalid"
    assert validity_mod.is_valid(valid) is True
    assert validity_mod.is_valid(invalid) is False


def test_execution_and_validity_are_independent():
    # A stage can execute successfully yet produce an invalid artifact: the two
    # outcomes are recorded and read separately.
    ran = rec.StageAttempted(run_id="r", occurred_at="t", stage_id="process", outcome="succeeded")
    produced = rec.ArtifactObserved(
        run_id="r", occurred_at="t", artifact_id="workbook",
        intended_digest=identity.digest("expected"), observed_digest=identity.digest("actual"),
    )
    validate_record(ran)
    assert ran.outcome == "succeeded"
    assert validity_mod.is_valid(produced) is False


def test_reuse_fingerprint_is_stable_and_binds_stage_and_source():
    fp1 = reuse_mod.reuse_fingerprint(stage_id="download", reused_from_run_id="run-1")
    fp2 = reuse_mod.reuse_fingerprint(stage_id="download", reused_from_run_id="run-1")
    fp_other = reuse_mod.reuse_fingerprint(stage_id="download", reused_from_run_id="run-2")
    assert fp1 == fp2
    assert fp1 != fp_other
    assert identity.is_digest(fp1)


def test_build_stage_reused_produces_a_valid_record():
    r = reuse_mod.build_stage_reused(
        run_id="run-3", occurred_at="t", stage_id="download", reused_from_run_id="run-1"
    )
    validate_record(r)
    assert r.reused_from_run_id == "run-1"
    assert r.fingerprint == reuse_mod.reuse_fingerprint(stage_id="download", reused_from_run_id="run-1")


# ---------------------------------------------------------------------------
# Summary projection over the evidence records
# ---------------------------------------------------------------------------

from mssp_pipeline.evidence import projection as proj_mod  # noqa: E402


def test_summary_projection_folds_records_into_a_run_view():
    records = [
        rec.RunStarted(run_id="run-1", occurred_at="t0", param_digests={"aco": identity.digest("C1234")}),
        rec.StageAttempted(run_id="run-1", occurred_at="t1", stage_id="download", outcome="succeeded"),
        reuse_mod.build_stage_reused(run_id="run-1", occurred_at="t2", stage_id="config", reused_from_run_id="run-0"),
        rec.MetricObserved(run_id="run-1", occurred_at="t3", stage_id="process", metric_id="rows_written", value=10),
        rec.PrerequisiteChecked(run_id="run-1", occurred_at="t4", prerequisite_id="bootstrap", observed_digest=identity.digest("true"), satisfied=True),
        rec.StageBlocked(run_id="run-1", occurred_at="t5", stage_id="promote", prerequisite_id="whitelist", reason="unsatisfied_prerequisite"),
        rec.ArtifactObserved(run_id="run-1", occurred_at="t6", artifact_id="workbook", intended_digest=identity.digest("v1"), observed_digest=identity.digest("v1")),
        rec.ArtifactObserved(run_id="run-1", occurred_at="t7", artifact_id="benchmark", intended_digest=identity.digest("a"), observed_digest=identity.digest("b")),
    ]
    summary = proj_mod.project_run_summary(records)

    assert summary.run_id == "run-1"
    assert summary.stage_execution["download"] == "succeeded"
    assert summary.reused["config"] == "run-0"
    assert summary.metrics["process"]["rows_written"] == 10
    assert summary.blocked["promote"] == "whitelist"
    # Validity is tracked per artifact, separately from execution.
    assert summary.artifact_validity["workbook"] == "valid"
    assert summary.artifact_validity["benchmark"] == "invalid"


def test_summary_projection_flags_missing_contract_metrics():
    contract = contract_mod.StageContract({"process": {"rows_written", "tables_written"}})
    records = [
        rec.MetricObserved(run_id="run-1", occurred_at="t", stage_id="process", metric_id="rows_written", value=3),
    ]
    summary = proj_mod.project_run_summary(records, contract=contract)
    # tables_written was declared but never observed.
    assert summary.missing_metrics["process"] == frozenset({"tables_written"})


def test_summary_projection_rejects_undeclared_metric_against_contract():
    contract = contract_mod.StageContract({"process": {"rows_written"}})
    records = [
        rec.MetricObserved(run_id="run-1", occurred_at="t", stage_id="process", metric_id="surprise", value=3),
    ]
    with pytest.raises(ValueError):
        proj_mod.project_run_summary(records, contract=contract)


# ---------------------------------------------------------------------------
# S-adapters: stage / resume / readiness / counts emit through the sink
# ---------------------------------------------------------------------------

from mssp_pipeline.evidence import adapters  # noqa: E402


def test_emit_run_started_digests_param_values():
    sink = sink_mod.InMemoryEvidenceSink()
    adapters.emit_run_started(sink, run_id="run-1", occurred_at="t", params={"aco": "C1234", "file_store": "s3://bucket/x"})
    (record,) = list(sink.records())
    assert isinstance(record, rec.RunStarted)
    # Raw values never enter evidence — only their digests.
    assert record.param_digests["aco"] == identity.digest("C1234")
    assert record.param_digests["file_store"] == identity.digest("s3://bucket/x")
    assert "s3://bucket/x" not in str(record.param_digests)


def test_emit_stage_attempt_and_reuse_go_through_the_sink():
    sink = sink_mod.InMemoryEvidenceSink()
    adapters.emit_stage_attempt(sink, run_id="run-1", occurred_at="t", stage_id="download", outcome="succeeded")
    adapters.emit_stage_reused(sink, run_id="run-1", occurred_at="t", stage_id="config", reused_from_run_id="run-0")
    kinds = [type(r).__name__ for r in sink.records()]
    assert kinds == ["StageAttempted", "StageReused"]


def test_readiness_adapter_emits_prerequisite_and_block_records():
    sink = sink_mod.InMemoryEvidenceSink()
    # Two prerequisites observed; one is not satisfied, so the stage is blocked.
    ready = adapters.evaluate_readiness(
        sink,
        run_id="run-1",
        occurred_at="t",
        stage_id="process",
        observations=[
            ("bootstrap", "true", True),
            ("whitelist", "false", False),
        ],
    )
    assert ready is False
    records = list(sink.records())
    prereqs = [r for r in records if isinstance(r, rec.PrerequisiteChecked)]
    blocks = [r for r in records if isinstance(r, rec.StageBlocked)]
    assert {p.prerequisite_id for p in prereqs} == {"bootstrap", "whitelist"}
    # The observed value is stored only as a digest (opaque prerequisite evidence).
    assert all(identity.is_digest(p.observed_digest) for p in prereqs)
    # The block names the unsatisfied prerequisite.
    assert [b.prerequisite_id for b in blocks] == ["whitelist"]


def test_readiness_adapter_reports_ready_when_all_satisfied():
    sink = sink_mod.InMemoryEvidenceSink()
    ready = adapters.evaluate_readiness(
        sink, run_id="run-1", occurred_at="t", stage_id="process",
        observations=[("bootstrap", "true", True)],
    )
    assert ready is True
    assert not [r for r in sink.records() if isinstance(r, rec.StageBlocked)]


def test_counts_adapter_emits_typed_metrics_validated_by_contract():
    sink = sink_mod.InMemoryEvidenceSink()
    contract = contract_mod.StageContract({"process": {"rows_written", "tables_written"}})
    adapters.emit_stage_counts(
        sink, run_id="run-1", occurred_at="t", stage_id="process",
        counts={"rows_written": 12, "tables_written": 3}, contract=contract,
    )
    metrics = [r for r in sink.records() if isinstance(r, rec.MetricObserved)]
    assert {(m.metric_id, m.value) for m in metrics} == {("rows_written", 12), ("tables_written", 3)}


def test_counts_adapter_rejects_metric_not_in_contract():
    sink = sink_mod.InMemoryEvidenceSink()
    contract = contract_mod.StageContract({"process": {"rows_written"}})
    with pytest.raises(ValueError):
        adapters.emit_stage_counts(
            sink, run_id="run-1", occurred_at="t", stage_id="process",
            counts={"surprise": 1}, contract=contract,
        )
