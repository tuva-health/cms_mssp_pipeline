"""
End-to-end integration tests for CCLFProcessor.

Uses synthetic zip fixtures (no PHI) with the DuckDB in-memory exporter.
"""

from datetime import date

import pytest

from mssp_pipeline.processing.defs.cclf_file_defs import CCLF_FILE_DEFS
from mssp_pipeline.processing.exporters.duckdb_exporter import DuckDBExporter
from mssp_pipeline.processing.processors.cclf_processor import CCLFProcessor
from tests.processing.conftest import ACO_ID, TEST_DATE, make_cclf_file

# Use ZC1 (parta_claims_header) as the representative CCLF file type.
ZC1_DEF = next(d for d in CCLF_FILE_DEFS if d.filename_pattern == "ZC1")

SAMPLE_ROWS = [
    {
        "CUR_CLM_UNIQ_ID": "0000000000001",
        "BENE_MBI_ID": "FAKEMBI0001",
        "PRVDR_OSCAR_NUM": "123456",
        "CLM_FROM_DT": "2025-01-01",
        "CLM_THRU_DT": "2025-01-31",
    },
    {
        "CUR_CLM_UNIQ_ID": "0000000000002",
        "BENE_MBI_ID": "FAKEMBI0002",
        "PRVDR_OSCAR_NUM": "654321",
        "CLM_FROM_DT": "2025-01-15",
        "CLM_THRU_DT": "2025-01-31",
    },
]


def _run_cclf(test_session, test_config, full_refresh=True):
    exporter = DuckDBExporter(schema="raw_data", full_refresh=full_refresh)
    CCLFProcessor(test_session, exporter, test_config).run()


def test_cclf_column_extraction(test_session, test_config, raw_dir):
    """Fixed-width columns are extracted at the correct positions."""
    make_cclf_file(raw_dir, ZC1_DEF, SAMPLE_ROWS)
    _run_cclf(test_session, test_config)

    rows = test_session.connection.execute(
        "SELECT CUR_CLM_UNIQ_ID, BENE_MBI_ID FROM raw_data.parta_claims_header ORDER BY 1"
    ).fetchall()

    assert len(rows) == 2
    assert rows[0][0].strip() == "0000000000001"
    assert rows[0][1].strip() == "FAKEMBI0001"
    assert rows[1][0].strip() == "0000000000002"
    assert rows[1][1].strip() == "FAKEMBI0002"


def test_cclf_file_date_parsed(test_session, test_config, raw_dir):
    """FILE_DATE is parsed from the .D<YYMMDD>. component of the filename."""
    make_cclf_file(raw_dir, ZC1_DEF, SAMPLE_ROWS[:1], date_str="250101")
    _run_cclf(test_session, test_config)

    file_date = test_session.connection.execute(
        "SELECT DISTINCT FILE_DATE FROM raw_data.parta_claims_header"
    ).fetchone()[0]

    assert file_date == TEST_DATE


def test_cclf_metadata_columns(test_session, test_config, raw_dir):
    """FILE_PATH has no zip:// prefix; DIRECTORY_NAME contains the bundle dir;
    FILE_NAME contains the CCLF type identifier."""
    make_cclf_file(raw_dir, ZC1_DEF, SAMPLE_ROWS[:1])
    _run_cclf(test_session, test_config)

    row = test_session.connection.execute(
        "SELECT FILE_PATH, DIRECTORY_NAME, FILE_NAME "
        "FROM raw_data.parta_claims_header LIMIT 1"
    ).fetchone()
    file_path, dir_name, file_name = row

    assert not file_path.startswith("zip://")
    assert f"P.{ACO_ID}.ACO" in dir_name  # bundle directory is present in path
    assert "ZC1" in file_name


def test_cclf_all_columns_varchar(test_session, test_config, raw_dir):
    """All output columns are stored as VARCHAR (no implicit type casting)."""
    make_cclf_file(raw_dir, ZC1_DEF, SAMPLE_ROWS[:1])
    _run_cclf(test_session, test_config)

    col_types = test_session.connection.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = 'raw_data' AND table_name = 'parta_claims_header' "
        "AND column_name NOT IN ('file_date')"
    ).fetchall()

    assert len(col_types) > 0, (
        "Expected columns in parta_claims_header but table is empty or missing"
    )
    for col_name, data_type in col_types:
        assert data_type == "VARCHAR", (
            f"Expected VARCHAR for {col_name}, got {data_type}"
        )
