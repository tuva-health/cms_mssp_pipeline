"""Generic emission adapters — the seam stages feed evidence through.

Emitting evidence is a generic mechanism; *what* to emit (the stage plan, the
concrete prerequisites and their sources, the populated metric contract) is
A-class policy. These adapters are the neutral half: given already-resolved
logical values, they build the right record and append it to an
:class:`~mssp_pipeline.evidence.sink.EvidenceSink`.

* ``emit_run_started``  — the run root, digesting every parameter value.
* ``emit_stage_attempt``— a stage's execution outcome.
* ``emit_stage_reused`` — a reused stage, with its reuse fingerprint.
* ``evaluate_readiness``— prerequisite observations → prerequisite/block records.
* ``emit_stage_counts`` — a stage's counts → typed metrics, contract-checked.

Observed prerequisite values are digested before they enter a record, so the
evidence stays opaque about the concrete value or the destination it came from.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Tuple

from mssp_pipeline.evidence.contract import StageContract, validate_metric
from mssp_pipeline.evidence.identity import digest
from mssp_pipeline.evidence.records import (
    ArtifactObserved,
    MetricObserved,
    PrerequisiteChecked,
    RunStarted,
    StageAttempted,
    StageBlocked,
)
from mssp_pipeline.evidence.reuse import build_stage_reused
from mssp_pipeline.evidence.sink import EvidenceSink


def emit_run_started(
    sink: EvidenceSink, *, run_id: str, occurred_at: str, params: Mapping[str, object]
) -> RunStarted:
    """Emit the run root, storing each parameter as a digest of its value."""
    record = RunStarted(
        run_id=run_id,
        occurred_at=occurred_at,
        param_digests={name: digest(str(value)) for name, value in params.items()},
    )
    sink.append(record)
    return record


def emit_stage_attempt(
    sink: EvidenceSink, *, run_id: str, occurred_at: str, stage_id: str, outcome: str
) -> StageAttempted:
    """Emit a stage's execution outcome (``succeeded`` / ``failed``)."""
    record = StageAttempted(run_id=run_id, occurred_at=occurred_at, stage_id=stage_id, outcome=outcome)
    sink.append(record)
    return record


def emit_stage_reused(
    sink: EvidenceSink, *, run_id: str, occurred_at: str, stage_id: str, reused_from_run_id: str
) -> None:
    """Emit a reused stage with its reuse fingerprint attached."""
    sink.append(
        build_stage_reused(
            run_id=run_id, occurred_at=occurred_at, stage_id=stage_id, reused_from_run_id=reused_from_run_id
        )
    )


def evaluate_readiness(
    sink: EvidenceSink,
    *,
    run_id: str,
    occurred_at: str,
    stage_id: str,
    observations: Iterable[Tuple[str, str, bool]],
) -> bool:
    """Record prerequisite observations and block the stage if any is unmet.

    ``observations`` is an iterable of ``(prerequisite_id, observed_value,
    satisfied)``. Each observation is recorded opaquely (the value is digested);
    every unsatisfied prerequisite produces a ``StageBlocked`` record naming it.
    Returns ``True`` iff every prerequisite is satisfied.
    """
    ready = True
    for prerequisite_id, observed_value, satisfied in observations:
        sink.append(
            PrerequisiteChecked(
                run_id=run_id,
                occurred_at=occurred_at,
                prerequisite_id=prerequisite_id,
                observed_digest=digest(str(observed_value)),
                satisfied=bool(satisfied),
            )
        )
        if not satisfied:
            ready = False
            sink.append(
                StageBlocked(
                    run_id=run_id,
                    occurred_at=occurred_at,
                    stage_id=stage_id,
                    prerequisite_id=prerequisite_id,
                    reason="unsatisfied_prerequisite",
                )
            )
    return ready


def emit_stage_counts(
    sink: EvidenceSink,
    *,
    run_id: str,
    occurred_at: str,
    stage_id: str,
    counts: Mapping[str, int],
    contract: StageContract,
) -> None:
    """Emit a stage's counts as typed metrics, checked against the contract.

    Every metric is validated against ``contract`` *before* anything is
    appended, so an undeclared metric id fails the whole batch closed rather
    than leaving a partial record set behind.
    """
    records = []
    for metric_id, value in counts.items():
        record = MetricObserved(
            run_id=run_id, occurred_at=occurred_at, stage_id=stage_id, metric_id=metric_id, value=value
        )
        validate_metric(record, contract)
        records.append(record)
    for record in records:
        sink.append(record)


def emit_artifact_observed(
    sink: EvidenceSink,
    *,
    run_id: str,
    occurred_at: str,
    artifact_id: str,
    intended_digest: str,
    observed_digest: str,
) -> ArtifactObserved:
    """Emit an intended-versus-observed artifact proof."""
    record = ArtifactObserved(
        run_id=run_id,
        occurred_at=occurred_at,
        artifact_id=artifact_id,
        intended_digest=intended_digest,
        observed_digest=observed_digest,
    )
    sink.append(record)
    return record
