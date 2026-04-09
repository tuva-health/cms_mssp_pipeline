"""
Tests for EXPUProcessor.

Each test creates a synthetic EXPU xlsx (with Table_1-*, Table_2-*, Table_3-* sheets)
using make_expu_xlsx() from conftest.py. No real PHI is used.

The processor reads with header=false and skip_empty_rows=false, so ALL rows
(including title/metadata rows) are returned, and columns are named A, B, C, ...
"""

from datetime import date

import pytest

from mssp_pipeline.processing.exporters.duckdb_exporter import DuckDBExporter
from mssp_pipeline.processing.processors.expu_processor import EXPUProcessor
from tests.processing.conftest import make_expu_xlsx

# ---------------------------------------------------------------------------
# Shared synthetic data
# ---------------------------------------------------------------------------

# Each "table sheet" has the same 6-row title block followed by data rows,
# matching the real report structure. header=false means all rows are data.

T1_ROWS = [
    ["Table 1", None, None, None],
    ["Medicare Shared Savings Program", None, None, None],
    ["Aggregate Expenditure/Utilization Report", None, None, None],
    ["ACO Name", None, None, None],
    ["2025 Quarter 1 Report", None, None, None],
    ["Table of Contents", None, None, None],
    [None, "ACO-Specific", "All MSSP ACOs", "National FFS"],
    ["Number of ACOs", "1", "332", "-"],
    ["Total Assigned Beneficiaries", "5727", "13464", "24663136"],
]  # 9 rows, 4 columns

T2_ROWS = [
    ["Table 2", None, None, None, None, None],
    ["Medicare Shared Savings Program", None, None, None, None, None],
    ["Regional Expenditures Report", None, None, None, None, None],
    ["ACO Name", None, None, None, None, None],
    ["2025 Quarter 1 Report", None, None, None, None, None],
    ["Table of Contents", None, None, None, None, None],
    [None, "Benchmark Year 3", "Q1", "Q2", "Q3", "Q4"],
    ["ESRD", "116855", "128604", None, None, None],
    ["Disabled", "19905", "23103", None, None, None],
]  # 9 rows, 6 columns

T3_ROWS = [
    ["Table 3", None, None, None, None, None, None, None],
    ["Medicare Shared Savings Program", None, None, None, None, None, None, None],
    ["SNF Report", None, None, None, None, None, None, None],
    ["ACO Name", None, None, None, None, None, None, None],
    ["2025 Quarter 1 Report", None, None, None, None, None, None, None],
    ["Table of Contents", None, None, None, None, None, None, None],
    [None, "ACO-Specific", None, None, None, None, "All MSSP", "National FFS"],
    ["Number of SNF Stays", None, None, None, None, None, None, None],
    ["Admissions", "349", None, None, None, None, "477.5", "1252332"],
]  # 9 rows, 8 columns


def _all_sheets(
    t1_rows=None, t2_rows=None, t3_rows=None,
    t1_name="Table_1-Aggregate_EU_Report",
    t2_name="Table_2-Regional_Expenditures",
    t3_name="Table_3-SNF_Report",
):
    """Build sheet_data dict for make_expu_xlsx."""
    return {
        "Cover": [["Cover page"]],
        t1_name: t1_rows or T1_ROWS,
        t2_name: t2_rows or T2_ROWS,
        t3_name: t3_rows or T3_ROWS,
    }


def _run(session, config, full_refresh):
    exporter = DuckDBExporter(schema="raw_data", full_refresh=full_refresh)
    EXPUProcessor(session, exporter, config).run()


def _count(session, table):
    return session.connection.execute(
        f"SELECT COUNT(*) FROM raw_data.{table}"
    ).fetchone()[0]


def _fetch(session, table, col="*"):
    return session.connection.execute(
        f"SELECT {col} FROM raw_data.{table}"
    ).fetchall()


# ---------------------------------------------------------------------------
# Column naming
# ---------------------------------------------------------------------------


def test_column_names_are_lowercase(test_session, test_config, raw_dir):
    """All output column names must be lowercase."""
    make_expu_xlsx(raw_dir, _all_sheets())
    _run(test_session, test_config, full_refresh=True)

    cols = [
        row[0]
        for row in test_session.connection.execute(
            "DESCRIBE raw_data.EXPU_TABLE_1"
        ).fetchall()
    ]
    for col in cols:
        assert col == col.lower(), f"Column {col!r} is not lowercase"


# ---------------------------------------------------------------------------
# Row counts
# ---------------------------------------------------------------------------


def test_row_count_table1(test_session, test_config, raw_dir):
    """All rows (including title rows) from Table_1 sheet are loaded."""
    make_expu_xlsx(raw_dir, _all_sheets())
    _run(test_session, test_config, full_refresh=True)
    assert _count(test_session, "EXPU_TABLE_1") == len(T1_ROWS)


def test_row_count_table2(test_session, test_config, raw_dir):
    """Table_2 is unpivoted: data rows (rows 8+) × period columns.
    T2_ROWS has 9 rows; 6 title+header rows are skipped; 2 data rows × 5 period cols = 10."""
    make_expu_xlsx(raw_dir, _all_sheets())
    _run(test_session, test_config, full_refresh=True)
    data_rows = len(T2_ROWS) - 7   # skip 6 title rows + 1 header row
    period_cols = 5                 # B, C, D, E, F
    assert _count(test_session, "EXPU_TABLE_2") == data_rows * period_cols


def test_row_count_table3(test_session, test_config, raw_dir):
    """All rows from Table_3 sheet are loaded."""
    make_expu_xlsx(raw_dir, _all_sheets())
    _run(test_session, test_config, full_refresh=True)
    assert _count(test_session, "EXPU_TABLE_3") == len(T3_ROWS)


# ---------------------------------------------------------------------------
# Sheet isolation
# ---------------------------------------------------------------------------


def test_sheets_are_isolated(test_session, test_config, raw_dir):
    """Table_1 rows must not appear in EXPU_TABLE_2 and vice versa.

    Table_1 keeps all rows including title rows; Table_2 is unpivoted so only
    data rows (ESRD, Disabled) appear in its A column.
    """
    make_expu_xlsx(raw_dir, _all_sheets())
    _run(test_session, test_config, full_refresh=True)

    t1_a_vals = {r[0] for r in _fetch(test_session, "EXPU_TABLE_1", "A")}
    t2_a_vals = {r[0] for r in _fetch(test_session, "EXPU_TABLE_2", "A")}

    # Table_1 still contains its title rows; Table_2's title rows are excluded after unpivot
    assert "Table 1" in t1_a_vals
    assert "Table 1" not in t2_a_vals
    # Table_2 data rows (ESRD, Disabled) should be present and absent from Table_1
    assert "ESRD" in t2_a_vals
    assert "ESRD" not in t1_a_vals


# ---------------------------------------------------------------------------
# Metadata columns
# ---------------------------------------------------------------------------


def test_file_date_q1(test_session, test_config, raw_dir):
    """Q1 -> last day of Q1 = March 31."""
    make_expu_xlsx(raw_dir, _all_sheets(), quarter="2025Q1")
    _run(test_session, test_config, full_refresh=True)

    file_date = test_session.connection.execute(
        "SELECT DISTINCT FILE_DATE FROM raw_data.EXPU_TABLE_1"
    ).fetchone()[0]
    assert file_date == date(2025, 3, 31), f"Expected 2025-03-31, got {file_date}"


def test_file_date_q4(test_session, test_config, raw_dir):
    """Q4 -> last day of Q4 = December 31."""
    make_expu_xlsx(raw_dir, _all_sheets(), quarter="2025Q4")
    _run(test_session, test_config, full_refresh=True)

    file_date = test_session.connection.execute(
        "SELECT DISTINCT FILE_DATE FROM raw_data.EXPU_TABLE_1"
    ).fetchone()[0]
    assert file_date == date(2025, 12, 31), f"Expected 2025-12-31, got {file_date}"


def test_period_column(test_session, test_config, raw_dir):
    """PERIOD column contains the literal quarter string from the filename."""
    make_expu_xlsx(raw_dir, _all_sheets(), quarter="2025Q1")
    _run(test_session, test_config, full_refresh=True)

    period = test_session.connection.execute(
        "SELECT DISTINCT PERIOD FROM raw_data.EXPU_TABLE_1"
    ).fetchone()[0]
    assert period == "2025Q1"


def test_file_path_metadata(test_session, test_config, raw_dir):
    """FILE_NAME ends with .xlsx; FILE_PATH and DIRECTORY_NAME are populated."""
    make_expu_xlsx(raw_dir, _all_sheets(), quarter="2025Q1")
    _run(test_session, test_config, full_refresh=True)

    row = test_session.connection.execute(
        "SELECT DISTINCT FILE_NAME, FILE_PATH, DIRECTORY_NAME "
        "FROM raw_data.EXPU_TABLE_1"
    ).fetchone()
    file_name, file_path, directory_name = row

    assert file_name.endswith(".xlsx"), f"FILE_NAME {file_name!r} should end with .xlsx"
    assert "EXPU" in file_path, f"FILE_PATH {file_path!r} should contain EXPU"
    assert file_name in file_path, "FILE_NAME should appear within FILE_PATH"
    assert directory_name, "DIRECTORY_NAME should be non-empty"


# ---------------------------------------------------------------------------
# Incremental deduplication
# ---------------------------------------------------------------------------


def test_incremental_second_run_skips(test_session, incremental_config, raw_dir):
    """Re-running with the same xlsx does not add duplicate rows."""
    make_expu_xlsx(raw_dir, _all_sheets(), quarter="2025Q1")
    _run(test_session, incremental_config, full_refresh=False)
    _run(test_session, incremental_config, full_refresh=False)

    assert _count(test_session, "EXPU_TABLE_1") == len(T1_ROWS)


def test_incremental_new_quarter_appends(test_session, incremental_config, raw_dir):
    """A new quarterly xlsx is appended; the existing quarter is not duplicated."""
    make_expu_xlsx(raw_dir, _all_sheets(), quarter="2025Q1", date_str="251001")
    _run(test_session, incremental_config, full_refresh=False)
    assert _count(test_session, "EXPU_TABLE_1") == len(T1_ROWS)

    make_expu_xlsx(raw_dir, _all_sheets(), quarter="2025Q2", date_str="251002")
    _run(test_session, incremental_config, full_refresh=False)
    assert _count(test_session, "EXPU_TABLE_1") == len(T1_ROWS) * 2


def test_full_refresh_replaces_data(
    test_session, incremental_config, test_config, raw_dir
):
    """Full refresh drops and recreates the table from current source files."""
    make_expu_xlsx(raw_dir, _all_sheets(), quarter="2025Q1", date_str="251001")
    _run(test_session, incremental_config, full_refresh=False)
    assert _count(test_session, "EXPU_TABLE_1") == len(T1_ROWS)

    _run(test_session, test_config, full_refresh=True)
    assert _count(test_session, "EXPU_TABLE_1") == len(T1_ROWS)


# ---------------------------------------------------------------------------
# Sheet prefix matching across delivery variants
# ---------------------------------------------------------------------------


def test_sheet_prefix_matching(test_session, test_config, raw_dir):
    """Sheet names with different suffixes after 'Table_1-' are still matched."""
    make_expu_xlsx(
        raw_dir,
        _all_sheets(
            t1_name="Table_1-Different_Name",
            t2_name="Table_2-Different_Name",
            t3_name="Table_3-Different_Name",
        ),
        quarter="2025Q1",
    )
    _run(test_session, test_config, full_refresh=True)

    # All three tables should load regardless of the suffix after "Table_N-"
    assert _count(test_session, "EXPU_TABLE_1") == len(T1_ROWS)
    # Table_2 is unpivoted: (9 - 7 title/header rows) × 5 period cols = 10
    assert _count(test_session, "EXPU_TABLE_2") == (len(T2_ROWS) - 7) * 5
    assert _count(test_session, "EXPU_TABLE_3") == len(T3_ROWS)


# ---------------------------------------------------------------------------
# Section column — Tables 1 & 3
# ---------------------------------------------------------------------------


def test_section_column_present_in_table1(test_session, test_config, raw_dir):
    """EXPU_TABLE_1 output must include a SECTION column."""
    make_expu_xlsx(raw_dir, _all_sheets())
    _run(test_session, test_config, full_refresh=True)

    cols = [
        r[0]
        for r in test_session.connection.execute(
            "DESCRIBE raw_data.EXPU_TABLE_1"
        ).fetchall()
    ]
    assert "section" in cols, "EXPU_TABLE_1 is missing the section column"


def test_section_column_present_in_table3(test_session, test_config, raw_dir):
    """EXPU_TABLE_3 output must include a section column."""
    make_expu_xlsx(raw_dir, _all_sheets())
    _run(test_session, test_config, full_refresh=True)

    cols = [
        r[0]
        for r in test_session.connection.execute(
            "DESCRIBE raw_data.EXPU_TABLE_3"
        ).fetchall()
    ]
    assert "section" in cols, "EXPU_TABLE_3 is missing the section column"


def test_no_section_column_in_table2(test_session, test_config, raw_dir):
    """EXPU_TABLE_2 (unpivot mode) must NOT have a section column."""
    make_expu_xlsx(raw_dir, _all_sheets())
    _run(test_session, test_config, full_refresh=True)

    cols = [
        r[0]
        for r in test_session.connection.execute(
            "DESCRIBE raw_data.EXPU_TABLE_2"
        ).fetchall()
    ]
    assert "section" not in cols, "EXPU_TABLE_2 should not have a section column"


# T3_ROWS row 8 is a section header ("Number of SNF Stays", B-D all null);
# row 9 ("Admissions") is a data row that should inherit that section label.
_T3_SECTION_ROWS = [
    ["Table 3", None, None, None, None, None, None, None],
    ["Medicare Shared Savings Program", None, None, None, None, None, None, None],
    ["SNF Report", None, None, None, None, None, None, None],
    ["ACO Name", None, None, None, None, None, None, None],
    ["2025 Quarter 1 Report", None, None, None, None, None, None, None],
    ["Table of Contents", None, None, None, None, None, None, None],
    [None, "ACO-Specific", None, None, None, None, "All MSSP", "National FFS"],
    ["Number of SNF Stays", None, None, None, None, None, None, None],  # section header
    ["Admissions", "349", None, None, None, None, "477.5", "1252332"],   # data row
]


def test_section_propagated_to_data_rows(test_session, test_config, raw_dir):
    """Data rows beneath a section-header row inherit its SECTION value.

    T3_ROWS row 8 ("Number of SNF Stays") is a section header.
    Row 9 ("Admissions") is the data row immediately below it.
    Both should get SECTION = 'Number of SNF Stays'.
    """
    make_expu_xlsx(
        raw_dir,
        _all_sheets(t3_rows=_T3_SECTION_ROWS),
        quarter="2025Q1",
    )
    _run(test_session, test_config, full_refresh=True)

    sections = test_session.connection.execute("""
        SELECT SECTION FROM raw_data.EXPU_TABLE_3
        WHERE A = 'Admissions'
    """).fetchall()

    assert sections, "Row 'Admissions' not found in EXPU_TABLE_3"
    for (section,) in sections:
        assert section == "Number of SNF Stays", (
            f"Expected SECTION='Number of SNF Stays', got {section!r}"
        )


def test_section_header_row_labeled_with_itself(test_session, test_config, raw_dir):
    """A section-header row's own SECTION value equals its A column value."""
    make_expu_xlsx(
        raw_dir,
        _all_sheets(t3_rows=_T3_SECTION_ROWS),
        quarter="2025Q1",
    )
    _run(test_session, test_config, full_refresh=True)

    section_row = test_session.connection.execute("""
        SELECT A, SECTION FROM raw_data.EXPU_TABLE_3
        WHERE A = 'Number of SNF Stays'
    """).fetchall()

    assert section_row, "Section-header row not found in EXPU_TABLE_3"
    for (a_val, section) in section_row:
        assert section == "Number of SNF Stays", (
            f"Section header row should have SECTION = its own A value, got {section!r}"
        )


# ---------------------------------------------------------------------------
# Unpivot — Table 2
# ---------------------------------------------------------------------------


def test_table2_has_period_column(test_session, test_config, raw_dir):
    """EXPU_TABLE_2 output must have a PERIOD_COLUMN column."""
    make_expu_xlsx(raw_dir, _all_sheets())
    _run(test_session, test_config, full_refresh=True)

    cols = [
        r[0]
        for r in test_session.connection.execute(
            "DESCRIBE raw_data.EXPU_TABLE_2"
        ).fetchall()
    ]
    assert "period_column" in cols


def test_table2_has_value_column(test_session, test_config, raw_dir):
    """EXPU_TABLE_2 output must have a VALUE column."""
    make_expu_xlsx(raw_dir, _all_sheets())
    _run(test_session, test_config, full_refresh=True)

    cols = [
        r[0]
        for r in test_session.connection.execute(
            "DESCRIBE raw_data.EXPU_TABLE_2"
        ).fetchall()
    ]
    assert "value" in cols


def test_table2_no_raw_period_columns(test_session, test_config, raw_dir):
    """After unpivot, the original lettered period columns (B, C, D, E, F) must not exist."""
    make_expu_xlsx(raw_dir, _all_sheets())
    _run(test_session, test_config, full_refresh=True)

    cols = {
        r[0]
        for r in test_session.connection.execute(
            "DESCRIBE raw_data.EXPU_TABLE_2"
        ).fetchall()
    }
    for letter in ("B", "C", "D", "E", "F"):
        assert letter not in cols, f"Column {letter!r} should not exist after unpivot"


def test_table2_unpivots_row_count(test_session, test_config, raw_dir):
    """Unpivot row count = data rows × period columns.

    T2_ROWS: 2 data rows (rows 8–9), 5 period columns (B–F) → 10 rows.
    """
    make_expu_xlsx(raw_dir, _all_sheets())
    _run(test_session, test_config, full_refresh=True)
    assert _count(test_session, "EXPU_TABLE_2") == 10


def test_table2_period_labels_from_header_row(test_session, test_config, raw_dir):
    """PERIOD_COLUMN values must match the strings in row 7 of the sheet."""
    make_expu_xlsx(raw_dir, _all_sheets())
    _run(test_session, test_config, full_refresh=True)

    # Row 7 of T2_ROWS: [None, "Benchmark Year 3", "Q1", "Q2", "Q3", "Q4"]
    expected_labels = {"Benchmark Year 3", "Q1", "Q2", "Q3", "Q4"}
    actual_labels = {
        r[0]
        for r in test_session.connection.execute(
            "SELECT DISTINCT PERIOD_COLUMN FROM raw_data.EXPU_TABLE_2"
        ).fetchall()
    }
    assert actual_labels == expected_labels, (
        f"Expected period labels {expected_labels}, got {actual_labels}"
    )


def test_table2_values_match_source(test_session, test_config, raw_dir):
    """Unpivoted VALUE for ESRD / Benchmark Year 3 matches the source cell."""
    make_expu_xlsx(raw_dir, _all_sheets())
    _run(test_session, test_config, full_refresh=True)

    # T2_ROWS row 8: ["ESRD", "116855", "128604", None, None, None]
    # column B = "Benchmark Year 3" → value = "116855"
    value = test_session.connection.execute("""
        SELECT VALUE FROM raw_data.EXPU_TABLE_2
        WHERE A = 'ESRD' AND PERIOD_COLUMN = 'Benchmark Year 3'
    """).fetchone()

    assert value is not None, "Row (ESRD, Benchmark Year 3) not found"
    assert value[0] == "116855", f"Expected '116855', got {value[0]!r}"
