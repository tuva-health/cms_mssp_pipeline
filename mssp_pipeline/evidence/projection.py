"""A summary projection over the append-only evidence records.

The evidence log is the source of truth; a projection is a read-only view
folded from it. ``project_run_summary`` reduces a record stream to a
``RunSummary`` — stage execution outcomes, reuse lineage, typed metrics per
stage, blocked stages, and per-artifact validity — optionally checking the
metrics against a named stage contract and flagging declared-but-missing ones.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from mssp_pipeline.evidence.contract import StageContract, missing_metrics, validate_metric
from mssp_pipeline.evidence.records import (
    ArtifactObserved,
    MetricObserved,
    PrerequisiteChecked,
    RunStarted,
    StageAttempted,
    StageBlocked,
    StageReused,
    _Record,
)
from mssp_pipeline.evidence.validity import artifact_validity


@dataclass
class RunSummary:
    run_id: Optional[str] = None
    param_digests: dict[str, str] = field(default_factory=dict)
    stage_execution: dict[str, str] = field(default_factory=dict)
    reused: dict[str, str] = field(default_factory=dict)
    metrics: dict[str, dict[str, int]] = field(default_factory=dict)
    prerequisites: dict[str, bool] = field(default_factory=dict)
    blocked: dict[str, str] = field(default_factory=dict)
    artifact_validity: dict[str, str] = field(default_factory=dict)
    missing_metrics: dict[str, frozenset[str]] = field(default_factory=dict)


def project_run_summary(
    records: Iterable[_Record], *, contract: Optional[StageContract] = None
) -> RunSummary:
    """Fold a record stream into a :class:`RunSummary`.

    When a ``contract`` is supplied, each metric is validated against it (an
    undeclared metric id raises) and declared-but-unobserved metric ids are
    reported per stage in ``missing_metrics``.
    """
    summary = RunSummary()
    observed_metric_ids: dict[str, set[str]] = {}

    for record in records:
        if isinstance(record, RunStarted):
            summary.run_id = record.run_id
            summary.param_digests = dict(record.param_digests)
        elif isinstance(record, StageAttempted):
            summary.stage_execution[record.stage_id] = record.outcome
        elif isinstance(record, StageReused):
            summary.reused[record.stage_id] = record.reused_from_run_id
        elif isinstance(record, MetricObserved):
            if contract is not None:
                validate_metric(record, contract)
            summary.metrics.setdefault(record.stage_id, {})[record.metric_id] = record.value
            observed_metric_ids.setdefault(record.stage_id, set()).add(record.metric_id)
        elif isinstance(record, PrerequisiteChecked):
            summary.prerequisites[record.prerequisite_id] = record.satisfied
        elif isinstance(record, StageBlocked):
            summary.blocked[record.stage_id] = record.prerequisite_id
        elif isinstance(record, ArtifactObserved):
            summary.artifact_validity[record.artifact_id] = artifact_validity(record)

    if contract is not None:
        for stage_id in contract.stages:
            gap = missing_metrics(stage_id, observed_metric_ids.get(stage_id, set()), contract)
            if gap:
                summary.missing_metrics[stage_id] = gap

    return summary
