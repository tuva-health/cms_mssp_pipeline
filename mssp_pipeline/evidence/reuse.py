"""Reuse fingerprints and strict reuse lineage.

When a stage is satisfied by reuse rather than re-execution, the evidence must
*prove* what was reused, not merely assert it. A reuse fingerprint is a digest
that binds the reused stage id to the run that originally performed it, so a
chain of resumes is verifiable after the fact.
"""

from __future__ import annotations

from mssp_pipeline.evidence.identity import digest, validate_logical_id
from mssp_pipeline.evidence.records import StageReused


def reuse_fingerprint(*, stage_id: str, reused_from_run_id: str) -> str:
    """A stable digest binding a reused stage to its source run."""
    validate_logical_id(stage_id)
    validate_logical_id(reused_from_run_id)
    return digest(f"{stage_id}|{reused_from_run_id}")


def build_stage_reused(
    *, run_id: str, occurred_at: str, stage_id: str, reused_from_run_id: str
) -> StageReused:
    """Construct a ``StageReused`` record with its reuse fingerprint attached."""
    return StageReused(
        run_id=run_id,
        occurred_at=occurred_at,
        stage_id=stage_id,
        reused_from_run_id=reused_from_run_id,
        fingerprint=reuse_fingerprint(stage_id=stage_id, reused_from_run_id=reused_from_run_id),
    )
