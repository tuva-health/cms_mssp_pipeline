"""Fail-closed validation of source identities and locations.

Every source value the pipeline splices into a filesystem path or a DuckDB
``glob()`` — the ACO id and the file store — is validated before use, on both
the ingest (download) side and the read (processing) side. A malformed value
must stop the run, never silently broaden a glob or escape its intended prefix.

These are the generic, client-neutral invariants. The pipeline enforces only
*safe, non-empty composition*: it deliberately does NOT impose any client's
identifier format (such as one-letter-then-four-digits). Stricter per-client
formats are downstream policy, not an upstream rule.

All fixtures here are synthetic; none reference any real client.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from mssp_pipeline.integration.remote_store import (
    parse_remote_store,
    validate_source_identifier,
)
from mssp_pipeline.processing.config_defs import validate_config
from mssp_pipeline.processing.session import DuckDBSession


# ---------------------------------------------------------------------------
# Remote store URI parsing — unknown scheme, query, fragment, empty segments,
# traversal, and glob operators are all rejected.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "uri",
    [
        "ftp://host/prefix",          # unknown scheme
        "http://host/prefix",         # unknown scheme
        "file:///prefix",             # unknown scheme
        "s3://bucket/prefix?q=1",     # query
        "s3://bucket/prefix#frag",    # fragment
        "s3:///prefix",               # empty authority
        "gs:///prefix",               # empty authority
        "s3://bucket/",               # trailing empty segment
        "s3://bucket//incoming",      # empty interior segment
        "s3://bucket/./incoming",     # dot segment
        "s3://bucket/../other",       # traversal segment
        "s3://bucket/incoming*",      # glob operator in path
        "gs://bucket/incoming?",      # glob operator in path
        "az://container/[abc]",       # glob operators in path
        "s3://buck*et/prefix",        # glob operator in authority
    ],
)
def test_parse_remote_store_rejects_unsafe_uris(uri):
    with pytest.raises(ValueError):
        parse_remote_store(uri)


@pytest.mark.parametrize(
    "uri",
    [
        "s3://bucket",
        "s3://bucket/prefix",
        "gs://bucket/incoming-data_2026.08/nested",
        "az://container/prefix",
        "azure://container/prefix",
        "abfss://container@account.dfs.core.windows.net/raw/year=2026",
    ],
)
def test_parse_remote_store_accepts_safe_uris(uri):
    assert parse_remote_store(uri).bucket


# ---------------------------------------------------------------------------
# Generic source identifier validation — safe, non-empty composition only.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        None,
        "a/b",            # path separator
        "a\\b",           # backslash separator
        "..",             # traversal
        ".",              # dot segment
        "id*",            # glob operator
        "id?",            # glob operator
        "[id]",           # glob operators
        "id name",        # whitespace
        "id ",            # trailing whitespace
        "'id'",           # quote
        '"id"',           # quote
    ],
)
def test_validate_source_identifier_rejects_unsafe_values(value):
    with pytest.raises(ValueError):
        validate_source_identifier(value, field_name="test id")


@pytest.mark.parametrize(
    "value",
    [
        "T0000",
        "C1234",
        "aco-123",       # generic: dash allowed, not client [A-Z][0-9]{4}
        "Z99",           # generic: not four digits
        "test_0001",     # generic: underscore allowed
        "A12345",        # generic: five digits
        "synthetic.aco", # generic: interior dot allowed
    ],
)
def test_validate_source_identifier_accepts_safe_generic_values(value):
    assert validate_source_identifier(value, field_name="test id") == value


# ---------------------------------------------------------------------------
# validate_config — required inputs fail closed, and both path-spliced values
# (ACO id, file store) are validated at the processing (read) boundary.
# ---------------------------------------------------------------------------

def _processing_config(**overrides):
    values = {
        "ACO_ID": "T0000",
        "OUTPUT_TYPE": "PARQUET",
        "OUTPUT_LOCATION": "/tmp/out",
        "FILE_STORE": "s3://synthetic-store",
        "AWS_REGION": "us-east-1",
        "GCS_KEY_ID": "",
        "GCS_SECRET": "",
        "AZURE_STORAGE_CONNECTION_STRING": "",
        "AZURE_STORAGE_ACCOUNT": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.parametrize("aco_id", ["", "   "])
def test_validate_config_fails_closed_on_empty_aco_id(aco_id):
    with pytest.raises(ValueError, match="MSSP_ACO_ID"):
        validate_config(_processing_config(ACO_ID=aco_id))


@pytest.mark.parametrize("file_store", ["", "   "])
def test_validate_config_fails_closed_on_empty_file_store(file_store):
    with pytest.raises(ValueError, match="MSSP_FILE_STORE"):
        validate_config(_processing_config(FILE_STORE=file_store))


@pytest.mark.parametrize("aco_id", ["a/b", "..", "id*", "id ", "'id'"])
def test_validate_config_rejects_unsafe_aco_id(aco_id):
    with pytest.raises(ValueError, match="MSSP_ACO_ID"):
        validate_config(_processing_config(ACO_ID=aco_id))


@pytest.mark.parametrize("aco_id", ["A0000", "aco-123", "Z99", "test_0001"])
def test_validate_config_does_not_impose_a_client_aco_format(aco_id):
    # The generic pipeline accepts any safe, non-empty id — the client
    # [A-Z][0-9]{4} rule must not be promoted upstream.
    validate_config(_processing_config(ACO_ID=aco_id))


@pytest.mark.parametrize(
    "file_store",
    [
        "ftp://host/prefix",          # unknown scheme
        "s3://bucket/prefix?q=1",     # query
        "s3://bucket/prefix#frag",    # fragment
        "s3://bucket//incoming",      # empty segment
        "s3://bucket/../other",       # traversal
        "s3://bucket/incoming*",      # glob operator
        "/tmp/store/*/incoming",      # glob operator in local path
    ],
)
def test_validate_config_rejects_unsafe_file_store(file_store):
    with pytest.raises(ValueError, match="MSSP_FILE_STORE"):
        validate_config(_processing_config(FILE_STORE=file_store))


@pytest.mark.parametrize(
    "file_store",
    [
        "/tmp/local-store",
        "relative/store",
        "s3://bucket/prefix",
        "gs://bucket/prefix",
    ],
)
def test_validate_config_accepts_safe_file_store(file_store):
    overrides = {}
    if file_store.startswith("gs://"):
        overrides = {"GCS_KEY_ID": "key", "GCS_SECRET": "secret"}
    validate_config(_processing_config(FILE_STORE=file_store, **overrides))



# ---------------------------------------------------------------------------
# Generic multi-cloud dispatch — each supported file-store scheme configures its
# own backend (abfss maps to Azure) and a local path configures no cloud backend.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("file_store", "backend"),
    [
        ("/tmp/local-store", None),
        ("s3://bucket/prefix", "s3"),
        ("az://container/prefix", "azure"),
        ("azure://container/prefix", "azure"),
        ("abfss://container@account.dfs.core.windows.net/prefix", "azure"),
        ("gs://bucket/prefix", "gcs"),
    ],
)
def test_load_extensions_dispatches_each_scheme_to_its_backend(file_store, backend):
    session = DuckDBSession.__new__(DuckDBSession)
    session._config = SimpleNamespace(FILE_STORE=file_store)
    session.connection = MagicMock()
    session._configure_s3 = MagicMock()
    session._configure_azure = MagicMock()
    session._configure_gcs = MagicMock()

    session._load_extensions()

    for name in ("s3", "azure", "gcs"):
        expected = 1 if name == backend else 0
        assert getattr(session, f"_configure_{name}").call_count == expected
