"""The raw CCLF tables medicare_cclf_connector reads, from its own sources file.

``fixtures/medicare_cclf_connector_sources.yml`` is the connector's
``models/_sources.yml``, copied byte for byte from the tag below. It is
vendored rather than fetched so the e2e job stays offline (see the header of
``.github/workflows/ci.yml``); the git blob id pins the copy to that tag, so a
hand edit fails loudly instead of quietly redefining the contract.

To move to a new connector release, copy the file from the new tag and update
the three constants:

    git -C <medicare_cclf_connector clone> fetch --tags
    git -C <clone> show <tag>:models/_sources.yml > tests/e2e/fixtures/medicare_cclf_connector_sources.yml
    git -C <clone> rev-parse <tag>:models/_sources.yml     # -> SOURCES_BLOB
    git -C <clone> rev-list -n1 <tag>                      # -> CONNECTOR_COMMIT
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

CONNECTOR_REPO = "https://github.com/tuva-health/medicare_cclf_connector"
CONNECTOR_TAG = "v1.0.0"
CONNECTOR_COMMIT = "1deb35646069679bffeee7abf12ce9ab0bf3e29c"
SOURCES_BLOB = "1b002460ad5b9dc45a8facbb482a59c711b4459e"  # <tag>:models/_sources.yml
SOURCES_PATH = Path(__file__).resolve().parent / "fixtures" / "medicare_cclf_connector_sources.yml"
SOURCE_NAME = "medicare_cclf"

# Connector sources the pipeline does not write, and why.
NOT_WRITTEN_BY_PIPELINE = {
    # The MSSP connector runs CCLF with `cms_alr_connector: true`, so eligibility
    # comes from the ALR connector; CCLF's `enrollment` source is never read.
    "enrollment": "eligibility comes from cms_alr_connector",
}

# Pipeline CCLF tables that are not connector sources, and why.
NOT_A_CONNECTOR_SOURCE = {
    "cclf0_summary_stats_header_record": "CCLF0 delivery summary; no connector model reads it",
}


def _git_blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def connector_source_columns(path: Path = SOURCES_PATH) -> dict[str, list[str]]:
    """Map each ``medicare_cclf`` source table to its declared column names."""
    data = path.read_bytes()
    blob = _git_blob_id(data)
    if blob != SOURCES_BLOB:
        raise ValueError(
            f"{path.name} is git blob {blob}, not {SOURCES_BLOB} "
            f"({CONNECTOR_TAG}:models/_sources.yml); re-copy it from the tag "
            "rather than editing it"
        )
    (source,) = [s for s in yaml.safe_load(data)["sources"] if s["name"] == SOURCE_NAME]
    return {
        table.get("identifier", table["name"]).lower(): [c["name"].lower() for c in table["columns"]]
        for table in source["tables"]
    }


def source_column_violations(
    written: dict[str, list[str]], declared: dict[str, list[str]] | None = None
) -> list[str]:
    """Compare the raw CCLF tables the pipeline wrote with the connector's sources.

    ``written`` maps each CCLF table the pipeline wrote to its column names.
    Every connector source must be written with at least its declared columns
    (extra pipeline metadata columns are fine), and every written CCLF table
    must be a connector source, so a rename on either side fails. Names
    compare exactly: the exporters write lowercase identifiers.
    """
    declared = connector_source_columns() if declared is None else declared
    violations: list[str] = []
    for table, columns in declared.items():
        if table in NOT_WRITTEN_BY_PIPELINE:
            continue
        if table not in written:
            violations.append(f"{table}: medicare_cclf_connector {CONNECTOR_TAG} source not written")
            continue
        missing = [c for c in columns if c not in written[table]]
        if missing:
            violations.append(
                f"{table}: missing medicare_cclf_connector {CONNECTOR_TAG} source columns {missing}"
            )
    for table in written:
        if table not in declared and table not in NOT_A_CONNECTOR_SOURCE:
            violations.append(
                f"{table}: written, but not a medicare_cclf_connector {CONNECTOR_TAG} source"
            )
    return violations
