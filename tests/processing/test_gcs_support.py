from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from mssp_pipeline.processing.config_defs import validate_config
from mssp_pipeline.processing.exporters.parquet_exporter import ParquetExporter
from mssp_pipeline.processing.session import DuckDBSession


def test_validate_config_accepts_gs_file_store():
    cfg = SimpleNamespace(
        ACO_ID="T0000",
        OUTPUT_TYPE="PARQUET",
        OUTPUT_LOCATION="/tmp/out",
        FILE_STORE="gs://bucket/base",
        AWS_REGION="us-east-1",
        GCS_KEY_ID="key-id",
        GCS_SECRET="secret",
        AZURE_STORAGE_CONNECTION_STRING="",
        AZURE_STORAGE_ACCOUNT="",
    )

    validate_config(cfg)


def test_parquet_exporter_treats_gs_as_cloud():
    exporter = ParquetExporter("gs://bucket/out")
    assert exporter._is_cloud is True


def test_duckdb_session_configures_gcs_secret(monkeypatch):
    fake_connection = MagicMock()
    fake_connection.execute.return_value = fake_connection

    with patch("duckdb.connect", return_value=fake_connection):
        DuckDBSession(
            SimpleNamespace(
                OUTPUT_TYPE="PARQUET",
                OUTPUT_LOCATION="/tmp/out",
                FILE_STORE="gs://bucket/base",
                GCS_CREDENTIALS_PATH="/tmp/creds.json",
                GCS_KEY_ID="key-id",
                GCS_SECRET="secret",
                GCS_PROJECT_ID="project-1",
            )
        )

    executed = " ".join(call.args[0] for call in fake_connection.execute.call_args_list)
    assert "INSTALL httpfs FROM core; LOAD httpfs;" in executed
    assert "TYPE GCS" in executed
    assert "KEY_ID 'key-id'" in executed
    assert "SECRET 'secret'" in executed
    assert "PROJECT_ID 'project-1'" in executed
