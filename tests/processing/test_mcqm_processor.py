"""
Tests for MCQMProcessor.

Each test creates a synthetic MCQM zip (xlsx with 5 sheets) using
make_mcqm_zip() from conftest.py. No real PHI is used.
"""

from datetime import date

import pytest

from mssp_pipeline.processing.defs.mcqm_file_defs import MCQM_FILE_DEFS
from mssp_pipeline.processing.exporters.duckdb_exporter import DuckDBExporter
from mssp_pipeline.processing.processors.mcqm_processor import MCQMProcessor
from tests.processing.conftest import make_mcqm_zip

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

BENE_HEADERS = [
    "BENE_MBI_ID",
    "BENE_1ST_NAME",
    "BENE_LAST_NAME",
    "BENE_SEX_CD",
    "BENE_BRTH_DT",
    "BENE_DEATH_DT",
    "VA_SELECTION_ONLY",
    "DM_AGE",
    "DM_DX",
    "DM_ENCOUNTER",
    "DM_EXCLUSION",
    "DM_FILTER",
    "BCS_AGE",
    "BCS_ENCOUNTER",
    "BCS_FEMALE",
    "BCS_EXCLUSION",
    "BCS_FILTER",
    "DEP_AGE",
    "DEP_ENCOUNTER",
    "DEP_EXCLUSION",
    "DEP_FILTER",
    "HTN_AGE",
    "HTN_DX",
    "HTN_ENCOUNTER",
    "HTN_EXCLUSION",
    "HTN_FILTER",
    "PROVIDER_1_NPI",
    "PROVIDER_2_NPI",
    "PROVIDER_3_NPI",
    "TOP_CLINIC_ID",
]

DM_HEADERS = [
    "BENE_MBI_ID",
    "BENE_1ST_NAME",
    "BENE_LAST_NAME",
    "BENE_SEX_CD",
    "BENE_BRTH_DT",
    "BENE_DEATH_DT",
    "VA_SELECTION_ONLY",
    "DM_AGE",
    "DM_DX",
    "DM_ENCOUNTER",
    "DM_EXCLUSION",
    "DM_FILTER",
    "PROVIDER_1_NPI",
    "PROVIDER_2_NPI",
    "PROVIDER_3_NPI",
    "TOP_CLINIC_ID",
]

BCS_HEADERS = [
    "BENE_MBI_ID",
    "BENE_1ST_NAME",
    "BENE_LAST_NAME",
    "BENE_SEX_CD",
    "BENE_BRTH_DT",
    "BENE_DEATH_DT",
    "VA_SELECTION_ONLY",
    "BCS_AGE",
    "BCS_ENCOUNTER",
    "BCS_FEMALE",
    "BCS_EXCLUSION",
    "BCS_FILTER",
    "PROVIDER_1_NPI",
    "PROVIDER_2_NPI",
    "PROVIDER_3_NPI",
    "TOP_CLINIC_ID",
]

DEP_HEADERS = [
    "BENE_MBI_ID",
    "BENE_1ST_NAME",
    "BENE_LAST_NAME",
    "BENE_SEX_CD",
    "BENE_BRTH_DT",
    "BENE_DEATH_DT",
    "VA_SELECTION_ONLY",
    "DEP_AGE",
    "DEP_ENCOUNTER",
    "DEP_EXCLUSION",
    "DEP_FILTER",
    "PROVIDER_1_NPI",
    "PROVIDER_2_NPI",
    "PROVIDER_3_NPI",
    "TOP_CLINIC_ID",
]

HTN_HEADERS = [
    "BENE_MBI_ID",
    "BENE_1ST_NAME",
    "BENE_LAST_NAME",
    "BENE_SEX_CD",
    "BENE_BRTH_DT",
    "BENE_DEATH_DT",
    "VA_SELECTION_ONLY",
    "HTN_AGE",
    "HTN_DX",
    "HTN_ENCOUNTER",
    "HTN_EXCLUSION",
    "HTN_FILTER",
    "PROVIDER_1_NPI",
    "PROVIDER_2_NPI",
    "PROVIDER_3_NPI",
    "TOP_CLINIC_ID",
]

ROW_A_BENE = [
    "1AA0A00AA00",
    "JANE",
    "DOE",
    "2",
    "19500101",
    None,
    "N",
    "Y",
    "Y",
    "Y",
    "N",
    "Y",
    "Y",
    "Y",
    "Y",
    "N",
    "Y",
    "Y",
    "Y",
    "N",
    "Y",
    "Y",
    "Y",
    "Y",
    "N",
    "Y",
    "1234567890",
    None,
    None,
    "CLINIC01",
]

ROW_B_BENE = [
    "2BB0B00BB00",
    "JOHN",
    "SMITH",
    "1",
    "19600202",
    None,
    "N",
    "Y",
    "N",
    "Y",
    "N",
    "Y",
    "N",
    "N",
    "N",
    "N",
    "N",
    "N",
    "N",
    "N",
    "N",
    "Y",
    "Y",
    "Y",
    "N",
    "Y",
    "9876543210",
    None,
    None,
    "CLINIC02",
]

ROW_A_DM = [
    "1AA0A00AA00",
    "JANE",
    "DOE",
    "2",
    "19500101",
    None,
    "N",
    "Y",
    "Y",
    "Y",
    "N",
    "Y",
    "1234567890",
    None,
    None,
    "CLINIC01",
]
ROW_B_DM = [
    "2BB0B00BB00",
    "JOHN",
    "SMITH",
    "1",
    "19600202",
    None,
    "N",
    "Y",
    "N",
    "Y",
    "N",
    "Y",
    "9876543210",
    None,
    None,
    "CLINIC02",
]


def _all_sheets(bene_rows, dm_rows=None):
    """Build a sheet_data dict for make_mcqm_zip with synthetic data on all 5 sheets."""
    dm_rows = dm_rows or bene_rows[:1]
    return {
        "Medicare_CQM_Beneficiaries": (BENE_HEADERS, bene_rows),
        "DM_001SSP": (DM_HEADERS, [r[:16] for r in dm_rows]),
        "BCS_112SSP": (BCS_HEADERS, [r[:16] for r in dm_rows]),
        "DEP_134SSP": (DEP_HEADERS, [r[:15] for r in dm_rows]),
        "HTN_236SSP": (HTN_HEADERS, [r[:16] for r in dm_rows]),
    }


def _run(session, config, full_refresh):
    exporter = DuckDBExporter(schema="raw_data", full_refresh=full_refresh)
    MCQMProcessor(session, exporter, config).run()


def _fetch(session, table, col="*"):
    return session.connection.execute(f"SELECT {col} FROM raw_data.{table}").fetchall()


def _count(session, table):
    return session.connection.execute(
        f"SELECT COUNT(*) FROM raw_data.{table}"
    ).fetchone()[0]


# ---------------------------------------------------------------------------
# Column naming
# ---------------------------------------------------------------------------


def test_column_names_are_uppercased(test_session, test_config, raw_dir):
    """All data column names in the output must be uppercased."""
    make_mcqm_zip(raw_dir, _all_sheets([ROW_A_BENE]))
    _run(test_session, test_config, full_refresh=True)

    cols = [
        row[0]
        for row in test_session.connection.execute(
            "DESCRIBE raw_data.MCQM_BENEFICIARIES"
        ).fetchall()
    ]
    for col in cols:
        assert col == col.upper(), f"Column {col!r} is not uppercase"


# ---------------------------------------------------------------------------
# Row counts
# ---------------------------------------------------------------------------


def test_beneficiaries_row_count(test_session, test_config, raw_dir):
    """All rows from the beneficiary sheet are loaded."""
    make_mcqm_zip(raw_dir, _all_sheets([ROW_A_BENE, ROW_B_BENE]))
    _run(test_session, test_config, full_refresh=True)

    assert _count(test_session, "MCQM_BENEFICIARIES") == 2


def test_measure_sheet_row_count(test_session, test_config, raw_dir):
    """Each measure sheet is loaded into its own table."""
    make_mcqm_zip(raw_dir, _all_sheets([ROW_A_BENE], dm_rows=[ROW_A_DM, ROW_B_DM]))
    _run(test_session, test_config, full_refresh=True)

    assert _count(test_session, "MCQM_DM_001SSP") == 2
    assert _count(test_session, "MCQM_BCS_112SSP") == 2


# ---------------------------------------------------------------------------
# Metadata columns
# ---------------------------------------------------------------------------


def test_file_date_is_last_day_of_quarter(test_session, test_config, raw_dir):
    """FILE_DATE should be the last calendar day of the encoded quarter."""
    make_mcqm_zip(raw_dir, _all_sheets([ROW_A_BENE]), quarter="2025Q4")
    _run(test_session, test_config, full_refresh=True)

    file_date = test_session.connection.execute(
        "SELECT DISTINCT FILE_DATE FROM raw_data.MCQM_BENEFICIARIES"
    ).fetchone()[0]
    assert file_date == date(2025, 12, 31), f"Expected 2025-12-31, got {file_date}"


def test_file_date_q1(test_session, test_config, raw_dir):
    """Q1 → March 31 of that year."""
    make_mcqm_zip(raw_dir, _all_sheets([ROW_A_BENE]), quarter="2025Q1")
    _run(test_session, test_config, full_refresh=True)

    file_date = test_session.connection.execute(
        "SELECT DISTINCT FILE_DATE FROM raw_data.MCQM_BENEFICIARIES"
    ).fetchone()[0]
    assert file_date == date(2025, 3, 31)


def test_period_column(test_session, test_config, raw_dir):
    """PERIOD column contains the literal quarter string from the filename."""
    make_mcqm_zip(raw_dir, _all_sheets([ROW_A_BENE]), quarter="2025Q4")
    _run(test_session, test_config, full_refresh=True)

    period = test_session.connection.execute(
        "SELECT DISTINCT PERIOD FROM raw_data.MCQM_BENEFICIARIES"
    ).fetchone()[0]
    assert period == "2025Q4"


def test_file_name_metadata(test_session, test_config, raw_dir):
    """FILE_NAME contains the xlsx filename, FILE_PATH contains the zip+xlsx path."""
    make_mcqm_zip(raw_dir, _all_sheets([ROW_A_BENE]))
    _run(test_session, test_config, full_refresh=True)

    row = test_session.connection.execute(
        "SELECT DISTINCT FILE_NAME, FILE_PATH FROM raw_data.MCQM_BENEFICIARIES"
    ).fetchone()
    file_name, file_path = row
    assert file_name.endswith(".xlsx"), f"FILE_NAME {file_name!r} should end with .xlsx"
    # FILE_PATH is zip_path/xlsx_name; the zip has no .zip extension in the new structure
    assert "MCQM" in file_path, (
        f"FILE_PATH {file_path!r} should contain the MCQM zip path"
    )
    assert file_name in file_path, "FILE_NAME should appear within FILE_PATH"


# ---------------------------------------------------------------------------
# Incremental deduplication
# ---------------------------------------------------------------------------


def test_incremental_second_run_skips(test_session, incremental_config, raw_dir):
    """A second run with the same source zip does not add duplicate rows."""
    make_mcqm_zip(raw_dir, _all_sheets([ROW_A_BENE, ROW_B_BENE]))
    _run(test_session, incremental_config, full_refresh=False)
    _run(test_session, incremental_config, full_refresh=False)

    assert _count(test_session, "MCQM_BENEFICIARIES") == 2


def test_incremental_new_quarter_appends(test_session, incremental_config, raw_dir):
    """A new quarterly zip is appended; the existing quarter is not duplicated."""
    # Each quarterly delivery has a unique outer-zip timestamp (date_str differs).
    make_mcqm_zip(
        raw_dir, _all_sheets([ROW_A_BENE]), quarter="2025Q3", date_str="259001"
    )
    _run(test_session, incremental_config, full_refresh=False)
    assert _count(test_session, "MCQM_BENEFICIARIES") == 1

    make_mcqm_zip(
        raw_dir, _all_sheets([ROW_B_BENE]), quarter="2025Q4", date_str="259002"
    )
    _run(test_session, incremental_config, full_refresh=False)
    assert _count(test_session, "MCQM_BENEFICIARIES") == 2


def test_full_refresh_replaces_data(
    test_session, incremental_config, test_config, raw_dir
):
    """A full-refresh run drops and recreates the table from the current source."""
    make_mcqm_zip(raw_dir, _all_sheets([ROW_A_BENE, ROW_B_BENE]))
    _run(test_session, incremental_config, full_refresh=False)
    assert _count(test_session, "MCQM_BENEFICIARIES") == 2

    _run(test_session, test_config, full_refresh=True)
    assert _count(test_session, "MCQM_BENEFICIARIES") == 2
