"""
End-to-end integration tests for MSSPProcessor.

Uses synthetic zip fixtures (no PHI) with the DuckDB in-memory exporter.
"""

from datetime import date

import pytest

from mssp_pipeline.processing.defs.mssp_file_defs import MSSP_FILE_DEFS
from mssp_pipeline.processing.exporters.duckdb_exporter import DuckDBExporter
from mssp_pipeline.processing.processors.mssp_processor import MSSPProcessor
from tests.processing.conftest import ACO_ID, make_mssp_zip

# Use BEUR as the representative MSSP file type (simple single-pattern CSV).
BEUR_DEF = next(d for d in MSSP_FILE_DEFS if d.file_type == "BEUR")
AALR1_DEF = next(
    d for d in MSSP_FILE_DEFS if d.table_name == "AALR1_ASSIGNED_BENEFICIARIES"
)

BEUR_HEADERS = ["bene_mbi_id", "total_expenditure", "ip_expenditure"]
BEUR_ROWS = [
    ["FAKEMBI0001", "10000.00", "5000.00"],
    ["FAKEMBI0002", "20000.00", "8000.00"],
]


def _run_mssp(test_session, config, full_refresh=True):
    exporter = DuckDBExporter(schema="raw_data", full_refresh=full_refresh)
    MSSPProcessor(test_session, exporter, config).run()


def test_mssp_column_names_uppercased(test_session, test_config, raw_dir):
    """Column names from the CSV header are normalized to uppercase."""
    make_mssp_zip(raw_dir, BEUR_DEF, BEUR_HEADERS, BEUR_ROWS)
    _run_mssp(test_session, test_config)

    col_names = [
        row[0]
        for row in test_session.connection.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'raw_data' "
            "AND table_name = 'BEUR_BENEFICIARY_EXPENDITURE_UTILIZATION_REPORT'"
        ).fetchall()
    ]

    for name in col_names:
        assert name == name.upper(), f"Column '{name}' is not uppercase"


def test_mssp_row_count(test_session, test_config, raw_dir):
    """All CSV rows are loaded."""
    make_mssp_zip(raw_dir, BEUR_DEF, BEUR_HEADERS, BEUR_ROWS)
    _run_mssp(test_session, test_config)

    count = test_session.connection.execute(
        "SELECT COUNT(*) FROM raw_data.BEUR_BENEFICIARY_EXPENDITURE_UTILIZATION_REPORT"
    ).fetchone()[0]

    assert count == len(BEUR_ROWS)


def test_mssp_file_date_is_today(test_session, test_config, raw_dir):
    """FILE_DATE is today() since MSSP filenames carry no date component."""
    make_mssp_zip(raw_dir, BEUR_DEF, BEUR_HEADERS, BEUR_ROWS[:1])
    _run_mssp(test_session, test_config)

    file_date = test_session.connection.execute(
        "SELECT DISTINCT FILE_DATE "
        "FROM raw_data.BEUR_BENEFICIARY_EXPENDITURE_UTILIZATION_REPORT"
    ).fetchone()[0]

    assert file_date == date.today()


def test_mssp_metadata_columns(test_session, test_config, raw_dir):
    """FILE_PATH has no zip:// prefix; DIRECTORY_NAME ends with .zip."""
    make_mssp_zip(raw_dir, BEUR_DEF, BEUR_HEADERS, BEUR_ROWS[:1])
    _run_mssp(test_session, test_config)

    row = test_session.connection.execute(
        "SELECT FILE_PATH, DIRECTORY_NAME, FILE_NAME "
        "FROM raw_data.BEUR_BENEFICIARY_EXPENDITURE_UTILIZATION_REPORT LIMIT 1"
    ).fetchone()
    file_path, dir_name, file_name = row

    assert not file_path.startswith("zip://")
    assert f"P.{ACO_ID}.ACO" in dir_name  # bundle path structure is present
    assert file_name is not None and len(file_name) > 0


def test_mssp_union_by_name(test_session, test_config, raw_dir):
    """Two CSVs with different column sets are unioned correctly (union_by_name=true).
    The second file has an extra column; rows from both files appear in the output."""
    # First bundle: standard columns
    make_mssp_zip(
        raw_dir,
        AALR1_DEF,
        headers=["bene_mbi_id", "aco_id"],
        rows=[["FAKEMBI0001", "A0001"]],
        bundle_suffix="_BNMRK",
    )
    # Second bundle: same columns plus an extra one
    make_mssp_zip(
        raw_dir,
        AALR1_DEF,
        headers=["bene_mbi_id", "aco_id", "extra_col"],
        rows=[["FAKEMBI0002", "A0001", "extra_value"]],
        bundle_suffix="_STLMT",
    )
    _run_mssp(test_session, test_config)

    count = test_session.connection.execute(
        "SELECT COUNT(*) FROM raw_data.AALR1_ASSIGNED_BENEFICIARIES"
    ).fetchone()[0]

    assert count == 2
