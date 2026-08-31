"""Append-only run-evidence core.

A run's lineage is recorded as an append-only graph of seven neutral record
types (see :mod:`mssp_pipeline.evidence.records`). The core owns the record
schema and validators, artifact identity and digests, evidence validity,
reuse fingerprints, the named stage contract for expected metric ids, a summary
projection, and the :class:`EvidenceSink` interface (with an atomic JSONL
development sink) plus the generic emission adapters.

The core is strictly client-neutral: it stores logical ids and digests only,
never concrete commands or destinations. Concrete stage plans, readiness paths,
destinations, and populated stage contracts are A-class policy supplied by a
downstream overlay, not by this package.
"""

from __future__ import annotations

from mssp_pipeline.evidence import adapters, contract, identity, projection, reuse, validity
from mssp_pipeline.evidence.contract import StageContract
from mssp_pipeline.evidence.projection import RunSummary, project_run_summary
from mssp_pipeline.evidence.records import (
    RECORD_TYPES,
    ArtifactObserved,
    MetricObserved,
    PrerequisiteChecked,
    RunStarted,
    StageAttempted,
    StageBlocked,
    StageReused,
    record_from_dict,
    validate_record,
)
from mssp_pipeline.evidence.sink import EvidenceSink, InMemoryEvidenceSink, JsonlEvidenceSink

__all__ = [
    "adapters",
    "contract",
    "identity",
    "projection",
    "reuse",
    "validity",
    "StageContract",
    "RunSummary",
    "project_run_summary",
    "RECORD_TYPES",
    "RunStarted",
    "StageAttempted",
    "StageReused",
    "PrerequisiteChecked",
    "StageBlocked",
    "MetricObserved",
    "ArtifactObserved",
    "record_from_dict",
    "validate_record",
    "EvidenceSink",
    "InMemoryEvidenceSink",
    "JsonlEvidenceSink",
]
