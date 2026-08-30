from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from mssp_pipeline.processing.session import DuckDBSession
from mssp_pipeline.processing.sql import sql_string_literal, validate_identifier


def test_sql_string_literal_escapes_single_quotes():
    assert sql_string_literal("a'b") == "'a''b'"


def test_validate_identifier_enforces_strict_allowlist():
    assert validate_identifier("raw_data") == "raw_data"
    with pytest.raises(ValueError):
        validate_identifier("raw-data")


def test_duckdb_session_escapes_temp_directory(monkeypatch, tmp_path):
    temp_dir = tmp_path / "staging'oops"
    fake_connection = MagicMock()

    monkeypatch.setattr(DuckDBSession, "_load_extensions", lambda self: None)
    with patch("duckdb.connect", return_value=fake_connection):
        DuckDBSession(
            SimpleNamespace(
                OUTPUT_TYPE="PARQUET",
                OUTPUT_LOCATION=str(tmp_path / "out"),
                FILE_STORE=str(tmp_path / "downloads"),
                TEMP_LOCATION=str(temp_dir),
            )
        )

    executed = [call.args[0] for call in fake_connection.execute.call_args_list]
    assert f"SET temp_directory = {sql_string_literal(str(temp_dir))};" in executed


def test_configure_s3_escapes_profile_region_and_credentials(monkeypatch):
    fake_connection = MagicMock()
    session = DuckDBSession.__new__(DuckDBSession)
    session.connection = fake_connection
    session._config = SimpleNamespace(
        AWS_PROFILE="prof'ile",
        AWS_REGION="us-west-'2",
        AWS_ACCESS_KEY_ID="key'id",
        AWS_SECRET_ACCESS_KEY="secret'key",
    )
    monkeypatch.setattr(session, "_sync_credentials_to_env", lambda region, profile: None)

    session._configure_s3()

    executed = "\n".join(call.args[0] for call in fake_connection.execute.call_args_list)
    profile = sql_string_literal("prof'ile")
    region = sql_string_literal("us-west-'2")
    key_id = sql_string_literal("key'id")
    secret = sql_string_literal("secret'key")
    assert f"CALL load_aws_credentials({profile});" in executed
    assert f"SET s3_region = {region};" in executed
    assert f"SET s3_access_key_id = {key_id};" in executed
    assert f"SET s3_secret_access_key = {secret};" in executed
    assert f"KEY_ID {key_id}" in executed
    assert f"SECRET {secret}" in executed
    assert f"REGION {region}" in executed


def test_configure_azure_escapes_connection_string():
    fake_connection = MagicMock()
    session = DuckDBSession.__new__(DuckDBSession)
    session.connection = fake_connection
    session._config = SimpleNamespace(
        AZURE_STORAGE_CONNECTION_STRING="DefaultEndpointsProtocol=https;AccountName=acct'01",
        AZURE_STORAGE_ACCOUNT="",
    )

    session._configure_azure()

    executed = "\n".join(call.args[0] for call in fake_connection.execute.call_args_list)
    assert sql_string_literal("DefaultEndpointsProtocol=https;AccountName=acct'01") in executed


def test_configure_gcs_escapes_secret_fields(monkeypatch):
    fake_connection = MagicMock()
    session = DuckDBSession.__new__(DuckDBSession)
    session.connection = fake_connection
    session._config = SimpleNamespace(
        GCS_CREDENTIALS_PATH="/tmp/creds'oops.json",
        GCS_KEY_ID="key'id",
        GCS_SECRET="secret'key",
        GCS_PROJECT_ID="project-'1",
    )

    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    session._configure_gcs()

    executed = "\n".join(call.args[0] for call in fake_connection.execute.call_args_list)
    key_id = sql_string_literal("key'id")
    secret = sql_string_literal("secret'key")
    project_id = sql_string_literal("project-'1")
    assert f"KEY_ID {key_id}" in executed
    assert f"SECRET {secret}" in executed
    assert f"PROJECT_ID {project_id}" in executed
