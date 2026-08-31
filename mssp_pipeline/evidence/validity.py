"""Evidence validity — read separately from execution outcome.

Execution ("did the stage run and succeed?") is carried by ``StageAttempted``.
Validity ("is the artifact the stage produced the one that was intended?") is a
distinct question, answered here from an ``ArtifactObserved`` record by
comparing its intended and observed digests. A stage can execute successfully
and still produce an invalid artifact; keeping the two apart is the point.
"""

from __future__ import annotations

from mssp_pipeline.evidence.records import ArtifactObserved


def artifact_validity(record: ArtifactObserved) -> str:
    """Return ``"valid"`` iff observed matches intended, else ``"invalid"``."""
    return "valid" if record.observed_digest == record.intended_digest else "invalid"


def is_valid(record: ArtifactObserved) -> bool:
    return artifact_validity(record) == "valid"
