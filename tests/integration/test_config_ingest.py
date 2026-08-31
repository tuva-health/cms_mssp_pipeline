"""Ingest-side validation of path-spliced source identities.

The download subsystem splices the ACO id into local paths and remote prefixes
(``{output_dir}/{aco}/{year}/{code}`` and ``{aco}/{year}/{code}``). That value
must be validated where the ingest config is built — the same safe-composition
rule the processing (read) side enforces — so a malformed id cannot escape its
intended location on the way in.

Fixtures are synthetic; no real client identity appears.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mssp_pipeline.integration.config import Config


def _config(**overrides):
    values = dict(
        aco="T0000",
        start_year=2025,
        output_dir=Path("/tmp/downloads"),
        state_file=Path("/tmp/state.json"),
    )
    values.update(overrides)
    return Config(**values)


@pytest.mark.parametrize("aco", ["", "   ", "a/b", "..", "id*", "id ", "'id'"])
def test_config_rejects_unsafe_aco_on_ingest(aco):
    with pytest.raises(ValueError):
        _config(aco=aco)


@pytest.mark.parametrize("aco", ["T0000", "C1234", "aco-123", "Z99"])
def test_config_accepts_safe_generic_aco_on_ingest(aco):
    assert _config(aco=aco).aco == aco


@pytest.mark.parametrize(
    "remote_store",
    [
        "ftp://host/prefix",        # unknown scheme
        "s3://bucket/prefix?q=1",   # query
        "s3://bucket/../other",     # traversal
        "s3://bucket/incoming*",    # glob operator
    ],
)
def test_config_rejects_unsafe_remote_store_on_ingest(remote_store):
    with pytest.raises(ValueError):
        _config(remote_store=remote_store)


def test_config_accepts_safe_remote_store_on_ingest():
    assert _config(remote_store="s3://bucket/prefix").remote_store == "s3://bucket/prefix"


def test_config_allows_no_remote_store():
    assert _config(remote_store=None).remote_store is None
