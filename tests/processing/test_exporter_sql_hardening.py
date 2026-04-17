from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mssp_pipeline.processing.exporters.duckdb_exporter import DuckDBExporter
from mssp_pipeline.processing.exporters.parquet_exporter import ParquetExporter
from mssp_pipeline.processing.exporters.base import string_literal


def test_duckdb_exporter_rejects_invalid_schema_name():
    with pytest.raises(ValueError):
        DuckDBExporter(schema="raw-data")


def test_parquet_exporter_escapes_destination_paths():
    exporter = ParquetExporter("/tmp/out'put")
    fake_connection = MagicMock()
    fake_connection.execute.return_value = fake_connection
    path = "/tmp/out'put/table.parquet"

    exporter._file_exists(fake_connection, path)

    executed = [call.args[0] for call in fake_connection.execute.call_args_list]
    assert f"SELECT 1 FROM read_parquet({string_literal(path)}) LIMIT 0" in executed
