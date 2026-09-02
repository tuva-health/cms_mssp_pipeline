from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip(
    "snowflake.connector",
    reason="requires the snowflake extra; production imports it lazily in build_exporter",
)

from mssp_pipeline.processing.exporters.snowflake_exporter import SnowflakeExporter  # noqa: E402


def _make_exporter() -> SnowflakeExporter:
    return SnowflakeExporter(
        sf_config=SimpleNamespace(
            username="user",
            account="acct",
            schema="RAW_DATA",
            database="DB",
            warehouse="WH",
            role="ROLE",
            rsa_key_path="/tmp/key.p8",
            rsa_key_passphrase="",
        ),
        staging_dir="/tmp",
        full_refresh=False,
    )


def test_get_missing_file_paths_returns_all_when_table_missing():
    exporter = _make_exporter()

    with patch.object(exporter, "_snowflake_table_exists", return_value=False):
        assert exporter.get_missing_file_paths("claims", ["a", "b"], None) == ["a", "b"]


def test_get_missing_file_paths_returns_only_missing_candidates():
    exporter = _make_exporter()
    fake_cursor = MagicMock()
    fake_cursor.fetchone.return_value = ("file_path",)
    fake_cursor.fetchall.return_value = [("b",)]
    fake_conn = MagicMock()
    fake_conn.cursor.return_value = fake_cursor

    with patch.object(exporter, "_snowflake_table_exists", return_value=True), \
         patch.object(exporter, "_load_rsa_key", return_value=b"key"), \
         patch("mssp_pipeline.processing.exporters.snowflake_exporter.connector.connect", return_value=fake_conn):
        missing = exporter.get_missing_file_paths("claims", ["a", "b", "c"], None)

    assert missing == ["b"]
    executed_sql = fake_cursor.execute.call_args.args[0]
    assert "WITH candidate_paths AS" in executed_sql
    assert "LEFT JOIN raw_data.claims" in executed_sql
    assert "SELECT 'a' AS \"file_path\"" in executed_sql
    assert "UNION ALL SELECT 'b'" in executed_sql
    assert "UNION ALL SELECT 'c'" in executed_sql
    assert 't."file_path" = c."file_path"' in executed_sql


def test_get_missing_file_paths_escapes_quotes():
    exporter = _make_exporter()
    fake_cursor = MagicMock()
    fake_cursor.fetchone.return_value = ("file_path",)
    fake_cursor.fetchall.return_value = []
    fake_conn = MagicMock()
    fake_conn.cursor.return_value = fake_cursor

    with patch.object(exporter, "_snowflake_table_exists", return_value=True), \
         patch.object(exporter, "_load_rsa_key", return_value=b"key"), \
         patch("mssp_pipeline.processing.exporters.snowflake_exporter.connector.connect", return_value=fake_conn):
        exporter.get_missing_file_paths("claims", ["a'b"], None)

    executed_sql = fake_cursor.execute.call_args.args[0]
    assert "SELECT 'a''b' AS \"file_path\"" in executed_sql


def test_get_missing_file_paths_supports_uppercase_file_path_columns():
    exporter = _make_exporter()
    fake_cursor = MagicMock()
    fake_cursor.fetchone.return_value = ("FILE_PATH",)
    fake_cursor.fetchall.return_value = [("b",)]
    fake_conn = MagicMock()
    fake_conn.cursor.return_value = fake_cursor

    with patch.object(exporter, "_snowflake_table_exists", return_value=True), \
         patch.object(exporter, "_load_rsa_key", return_value=b"key"), \
         patch("mssp_pipeline.processing.exporters.snowflake_exporter.connector.connect", return_value=fake_conn):
        missing = exporter.get_missing_file_paths("claims", ["a", "b"], None)

    assert missing == ["b"]
    executed_sql = fake_cursor.execute.call_args.args[0]
    assert 'SELECT c."FILE_PATH"' in executed_sql
    assert 't."FILE_PATH" = c."FILE_PATH"' in executed_sql


def test_full_refresh_creates_uppercase_columns():
    """A recreated table must have UPPERCASE columns.

    DuckDB folds the uppercase fixed-width column names to lowercase in the
    staged Parquet; a bare INFER_SCHEMA would then create case-sensitive
    lowercase-quoted columns, which the connector's unquoted (UPPERCASE-folded)
    dbt references cannot resolve. The CREATE must uppercase the inferred column
    names, and the load must still match the lowercase Parquet case-insensitively.
    """
    exporter = _make_exporter()
    fake_cursor = MagicMock()
    fake_conn = MagicMock()
    fake_conn.cursor.return_value = fake_cursor

    with patch.object(exporter, "_load_rsa_key", return_value=b"key"), \
         patch("mssp_pipeline.processing.exporters.snowflake_exporter.connector.connect", return_value=fake_conn):
        exporter._upload_to_snowflake("/tmp/parta_claims_header.parquet", "parta_claims_header")

    sqls = [c.args[0] for c in fake_cursor.execute.call_args_list]
    create_sql = next(s for s in sqls if "CREATE OR REPLACE TABLE" in s)
    assert 'UPPER("COLUMN_NAME")' in create_sql
    # OBJECT_CONSTRUCT(*) would carry the lowercase names verbatim -- it must not
    # be used for the column template any more.
    assert "OBJECT_CONSTRUCT(*)" not in create_sql
    copy_sql = next(s for s in sqls if "COPY INTO" in s)
    assert "MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE" in copy_sql
