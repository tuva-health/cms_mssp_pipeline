"""The seven neutral run-evidence record types and their validators.

A run's lineage is an append-only graph of exactly seven record types:

* ``RunStarted``           — the run root; logical parameter digests only.
* ``StageAttempted``       — a stage's *execution* outcome (succeeded/failed).
* ``StageReused``          — a stage satisfied by reuse, with its lineage and a
                             reuse fingerprint.
* ``PrerequisiteChecked``  — an opaque prerequisite observation (digest only).
* ``StageBlocked``         — a stage blocked by an unsatisfied prerequisite.
* ``MetricObserved``       — a typed metric keyed by a named stage contract.
* ``ArtifactObserved``     — an intended-versus-observed artifact proof, from
                             which *validity* (distinct from execution) is read.

Every record is a frozen value carrying logical ids and digests only. The
validators enforce that: an id that looks like a destination or command, or a
digest field that is not a hex SHA-256, is rejected. Concrete commands,
destinations, and stage plans are A-class policy and never appear here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Mapping

from mssp_pipeline.evidence.identity import is_digest, validate_logical_id


RECORD_TYPES: tuple[str, ...] = (
    "RunStarted",
    "StageAttempted",
    "StageReused",
    "PrerequisiteChecked",
    "StageBlocked",
    "MetricObserved",
    "ArtifactObserved",
)

EXECUTION_OUTCOMES: frozenset[str] = frozenset({"succeeded", "failed"})


@dataclass(frozen=True)
class _Record:
    run_id: str
    occurred_at: str

    @property
    def record_type(self) -> str:
        return type(self).__name__

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["record_type"] = self.record_type
        return payload


@dataclass(frozen=True)
class RunStarted(_Record):
    """Root of a run's evidence graph: logical parameter digests only."""

    param_digests: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class StageAttempted(_Record):
    """A stage's *execution* outcome — that it ran, and whether it succeeded."""

    stage_id: str = ""
    outcome: str = ""


@dataclass(frozen=True)
class StageReused(_Record):
    """A stage satisfied by reuse from a prior run, with its lineage proof."""

    stage_id: str = ""
    reused_from_run_id: str = ""
    fingerprint: str = ""


@dataclass(frozen=True)
class PrerequisiteChecked(_Record):
    """An opaque prerequisite observation — the digest of what was seen."""

    prerequisite_id: str = ""
    observed_digest: str = ""
    satisfied: bool = False


@dataclass(frozen=True)
class StageBlocked(_Record):
    """A stage blocked because a prerequisite was not satisfied."""

    stage_id: str = ""
    prerequisite_id: str = ""
    reason: str = "unsatisfied_prerequisite"


@dataclass(frozen=True)
class MetricObserved(_Record):
    """A typed metric emitted by a stage, keyed by a named stage contract."""

    stage_id: str = ""
    metric_id: str = ""
    value: int = 0


@dataclass(frozen=True)
class ArtifactObserved(_Record):
    """Intended-versus-observed artifact proof; validity is read from this."""

    artifact_id: str = ""
    intended_digest: str = ""
    observed_digest: str = ""


_BY_NAME: dict[str, type[_Record]] = {
    "RunStarted": RunStarted,
    "StageAttempted": StageAttempted,
    "StageReused": StageReused,
    "PrerequisiteChecked": PrerequisiteChecked,
    "StageBlocked": StageBlocked,
    "MetricObserved": MetricObserved,
    "ArtifactObserved": ArtifactObserved,
}


def _require_text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _require_digest(name: str, value: object) -> None:
    if not is_digest(value):
        raise ValueError(f"{name} must be a hex SHA-256 digest, got {value!r}")


def validate_record(record: _Record) -> _Record:
    """Validate a record's neutrality and shape, or raise ``ValueError``.

    Enforces logical-id charset on every id field, hex-digest shape on every
    digest field, and the small closed vocabularies (execution outcome). This
    is what keeps commands and destinations out of the evidence core.
    """
    _require_text("run_id", record.run_id)
    _require_text("occurred_at", record.occurred_at)

    if isinstance(record, RunStarted):
        for name, value in record.param_digests.items():
            validate_logical_id(name)
            _require_digest(f"param_digests[{name!r}]", value)

    elif isinstance(record, StageAttempted):
        validate_logical_id(record.stage_id)
        if record.outcome not in EXECUTION_OUTCOMES:
            raise ValueError(
                f"outcome must be one of {sorted(EXECUTION_OUTCOMES)}, got {record.outcome!r}"
            )

    elif isinstance(record, StageReused):
        validate_logical_id(record.stage_id)
        validate_logical_id(record.reused_from_run_id)
        _require_digest("fingerprint", record.fingerprint)

    elif isinstance(record, PrerequisiteChecked):
        validate_logical_id(record.prerequisite_id)
        _require_digest("observed_digest", record.observed_digest)
        if not isinstance(record.satisfied, bool):
            raise ValueError("satisfied must be a boolean")

    elif isinstance(record, StageBlocked):
        validate_logical_id(record.stage_id)
        validate_logical_id(record.prerequisite_id)
        validate_logical_id(record.reason)

    elif isinstance(record, MetricObserved):
        validate_logical_id(record.stage_id)
        validate_logical_id(record.metric_id)
        if not isinstance(record.value, int) or isinstance(record.value, bool):
            raise ValueError("metric value must be an integer")

    elif isinstance(record, ArtifactObserved):
        validate_logical_id(record.artifact_id)
        _require_digest("intended_digest", record.intended_digest)
        _require_digest("observed_digest", record.observed_digest)

    else:  # pragma: no cover - defensive
        raise ValueError(f"Unknown record type: {type(record).__name__}")

    return record


def record_from_dict(payload: Mapping[str, object]) -> _Record:
    """Reconstruct a record from its serialized dict (inverse of ``to_dict``)."""
    data = dict(payload)
    type_name = data.pop("record_type", None)
    if type_name not in _BY_NAME:
        raise ValueError(f"Unknown record_type: {type_name!r}")
    cls = _BY_NAME[type_name]
    allowed = {f.name for f in fields(cls)}
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f"Unexpected fields for {type_name}: {sorted(unknown)}")
    return cls(**data)
