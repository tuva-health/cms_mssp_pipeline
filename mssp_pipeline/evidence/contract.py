"""The named stage contract for expected metric ids.

The core owns the *type* and the *validation*: a ``StageContract`` maps each
stage id to the set of metric ids that stage is expected to emit, using logical
ids only. A ``MetricObserved`` whose metric id is not declared for its stage is
rejected, and metrics a stage declared but never emitted are surfaced by
``missing_metrics`` for the summary projection to flag.

The *populated* contract — which stages exist and which metric ids they own — is
A-class policy supplied by a downstream overlay. This package ships only the
type and its rules; tests use a synthetic contract.
"""

from __future__ import annotations

from typing import Mapping, Iterable

from mssp_pipeline.evidence.identity import validate_logical_id
from mssp_pipeline.evidence.records import MetricObserved


class StageContract:
    """An immutable ``stage_id -> frozenset[metric_id]`` declaration."""

    def __init__(self, expected: Mapping[str, Iterable[str]]):
        built: dict[str, frozenset[str]] = {}
        for stage_id, metric_ids in expected.items():
            validate_logical_id(stage_id)
            ids = frozenset(metric_ids)
            for metric_id in ids:
                validate_logical_id(metric_id)
            built[stage_id] = ids
        self._expected = built

    def expected(self, stage_id: str) -> frozenset[str]:
        """The metric ids declared for ``stage_id`` (empty if undeclared)."""
        return self._expected.get(stage_id, frozenset())

    def declares(self, stage_id: str, metric_id: str) -> bool:
        return metric_id in self.expected(stage_id)

    @property
    def stages(self) -> frozenset[str]:
        return frozenset(self._expected)


def validate_metric(record: MetricObserved, contract: StageContract) -> MetricObserved:
    """Raise unless ``record``'s metric id is declared for its stage."""
    if not contract.declares(record.stage_id, record.metric_id):
        raise ValueError(
            f"Metric {record.metric_id!r} is not declared for stage "
            f"{record.stage_id!r} by the stage contract"
        )
    return record


def missing_metrics(
    stage_id: str, observed_metric_ids: Iterable[str], contract: StageContract
) -> frozenset[str]:
    """Metric ids the contract declares for ``stage_id`` but were not observed."""
    return contract.expected(stage_id) - frozenset(observed_metric_ids)
