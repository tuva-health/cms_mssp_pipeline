"""
STLMTProcessor: the final settlement (financial reconciliation) workbook (TUVA-158).

The settlement bundle's 'P.{ACO}.ACO.STLMT.Y{PY}.D...T....xlsx' workbook holds
CMS's final result for a performance year. Its 'Table *' sheets and its
Parameters tab load into STLMT_* cell-grain tables through the shared
sectioned-sheet grammar; Cover, TOC and Glossary do not.

Tested through the processor's output tables (raw_data.stlmt_*):

- each STLMT table holds its own sheet, and only the documented sheets load;
- discovery keys on the '.STLMT.Y' filename token and ignores the bundle's
  other workbooks;
- PERFORMANCE_YEAR, SUBMISSION_ID and IS_EMBARGOED identify the delivery, and
  the embargoed and unembargoed copies both load with the unembargoed one
  identifiable as preferred;
- Table 2, Table 3 and Table A1 keep their lettered rows (single and double
  letters) and per-enrollment-type rows apart, which is what the connector
  stages.

Every fixture is synthetic.
"""

from datetime import date

import pytest

from mssp_pipeline.processing.exporters.duckdb_exporter import DuckDBExporter
from mssp_pipeline.processing.processors.stlmt_processor import STLMTProcessor

from .conftest import ACO_ID
from .test_aco_workbook_processors import (
    config_for,
    counts,
    make_bnmrk_bundle,
    run,
    write_workbook,
)
from .test_settlement_submission import (
    BY3_RISK_SCORE,
    EMBARGOED,
    PY_RISK_SCORE,
    STLMT_SHEET_BY_TABLE,
    TYPE_BENCHMARK,
    TYPES,
    UNEMBARGOED,
    UPDATED_BENCHMARK,
    make_stlmt_bundle,
    stlmt_sheets,
)

STLMT_TABLES = set(STLMT_SHEET_BY_TABLE) | {"stlmt_parameters"}


# ===========================================================================
# Table -> sheet mapping
# ===========================================================================


@pytest.mark.parametrize(
    "table,tag", [(t, v[1]) for t, v in STLMT_SHEET_BY_TABLE.items()]
)
def test_each_stlmt_table_holds_its_own_sheet(
    test_session, test_config, raw_dir, table, tag
):
    """Each fixture sheet carries a section and row label unique to it, so a
    pattern pointing at the wrong sheet fails here. 'Table 1 - %' must not
    take Table 1A-1C, and 'Table A1 - %' must not take Table 1."""
    make_stlmt_bundle(raw_dir)
    conn = run(STLMTProcessor, test_session, test_config)

    assert conn.execute(
        f"SELECT COUNT(*) FROM raw_data.{table} "
        f"WHERE section_label = 'Table {tag} Section' "
        f"AND row_label = 'Row Unique To Table {tag}'"
    ).fetchone()[0] > 0
    assert conn.execute(
        f"SELECT COUNT(*) FROM raw_data.{table} "
        f"WHERE row_label LIKE 'Row Unique To Table %' "
        f"AND row_label <> 'Row Unique To Table {tag}'"
    ).fetchone()[0] == 0


def test_only_the_table_sheets_and_parameters_load(
    test_session, test_config, raw_dir
):
    """Cover, TOC and Glossary have no table; every other sheet has one."""
    make_stlmt_bundle(raw_dir)
    conn = run(STLMTProcessor, test_session, test_config)

    assert set(counts(conn)) == STLMT_TABLES


def test_parameters_is_read_as_key_values(test_session, test_config, raw_dir):
    make_stlmt_bundle(raw_dir)
    conn = run(STLMTProcessor, test_session, test_config)

    assert conn.execute(
        "SELECT value_text FROM raw_data.stlmt_parameters WHERE row_label = 'ACO Track'"
    ).fetchone()[0] == "BASIC Level A"


def test_footnote_and_notes_rows_are_dropped(test_session, test_config, raw_dir):
    make_stlmt_bundle(raw_dir)
    conn = run(STLMTProcessor, test_session, test_config)

    for table in STLMT_SHEET_BY_TABLE:
        assert conn.execute(
            f"SELECT COUNT(*) FROM raw_data.{table} "
            f"WHERE row_label LIKE 'Note%' OR row_label LIKE '%ootnote%' "
            f"OR row_label LIKE 'Weighted%' OR row_label LIKE 'Earned payment%'"
        ).fetchone()[0] == 0, table


def test_a_workbook_without_table_a6_does_not_fail_the_run(
    test_session, test_config, raw_dir
):
    """ACPT applies only to some agreements, so Table A6 may be absent. The
    table still materialises, empty."""
    make_stlmt_bundle(raw_dir, with_table_a6=False)
    conn = run(STLMTProcessor, test_session, test_config)

    assert counts(conn)["stlmt_table_a6"] == 0
    assert counts(conn)["stlmt_table_3"] > 0


# ===========================================================================
# Discovery
# ===========================================================================


def test_glob_finds_only_the_settlement_workbook(test_session, test_config, raw_dir):
    """The settlement AEXPU beside it, and every benchmark bundle file, are
    other processors' files."""
    make_stlmt_bundle(raw_dir)
    make_bnmrk_bundle(raw_dir)
    conn = run(STLMTProcessor, test_session, test_config)

    assert conn.execute(
        "SELECT DISTINCT file_name FROM raw_data.stlmt_table_3"
    ).fetchall() == [(f"P.{ACO_ID}.ACO.STLMT.Y2025.D259999.T1111111.xlsx",)]


def test_another_workbook_carrying_the_token_is_skipped(
    test_session, test_config, raw_dir
):
    """Only 'STLMT.Y<year>' followed directly by the stamp is the financial
    reconciliation workbook. Another workbook whose name merely contains the
    token is not read as one."""
    bundle = make_stlmt_bundle(raw_dir)
    write_workbook(
        bundle / f"P.{ACO_ID}.ACO.STLMT.Y2025.QUALITY.D259999.T1111111.xlsx",
        stlmt_sheets(),
    )
    conn = run(STLMTProcessor, test_session, test_config)

    assert conn.execute(
        "SELECT COUNT(DISTINCT file_name) FROM raw_data.stlmt_table_3"
    ).fetchone()[0] == 1


def test_a_filename_year_that_disagrees_with_the_directory_is_reported(
    test_session, test_config, raw_dir, capsys
):
    """PERFORMANCE_YEAR follows the directory, as the settlement AEXPU's does.
    A workbook whose 'Y<year>' names another year still loads, with a warning."""
    write_workbook(
        raw_dir / ACO_ID / "2026" / "01" / f"P.{ACO_ID}.ACO.STLMT.D259999.T1111111"
        / f"P.{ACO_ID}.ACO.STLMT.Y2025.D259999.T1111111.xlsx",
        stlmt_sheets(),
    )
    conn = run(STLMTProcessor, test_session, test_config)

    assert "filename says performance year 2025 but it is filed under 2026" in (
        capsys.readouterr().out
    )
    assert conn.execute(
        "SELECT DISTINCT performance_year, period FROM raw_data.stlmt_table_3"
    ).fetchall() == [("2026", "Y2025")]


def test_missing_file_store_is_not_an_error(test_session, test_config, raw_dir):
    run(STLMTProcessor, test_session, test_config)
    assert counts(test_session.connection) == {}


def test_uses_the_expu_batch_size_knob(test_session, raw_dir):
    config = config_for(raw_dir, PROCESS_BATCH_SIZE_DEFAULT=99, PROCESS_BATCH_SIZE_EXPU=7)
    processor = STLMTProcessor(test_session, DuckDBExporter(), config)
    assert processor._batch_size_for() == 7


# ===========================================================================
# Delivery metadata
# ===========================================================================


def test_metadata_columns(test_session, test_config, raw_dir):
    """PERFORMANCE_YEAR from the layout, the settlement SUBMISSION_ID and
    embargo flag, PERIOD the 'Y<PY>' token, FILE_DATE the D stamp."""
    make_stlmt_bundle(raw_dir, year="2025", date_str="251015")
    conn = run(STLMTProcessor, test_session, test_config)

    assert conn.execute(
        "SELECT DISTINCT aco_id, performance_year, benchmark_year, submission_id, "
        "period, file_date, is_embargoed FROM raw_data.stlmt_table_3"
    ).fetchall() == [
        (ACO_ID, "2025", None, "STLMT.D251015.T1111111", "Y2025",
         date(2025, 10, 15), False)
    ]


def test_placeholder_stamp_gives_a_null_file_date(test_session, test_config, raw_dir):
    make_stlmt_bundle(raw_dir, date_str="259999")
    conn = run(STLMTProcessor, test_session, test_config)

    assert conn.execute(
        "SELECT DISTINCT file_date FROM raw_data.stlmt_table_3"
    ).fetchall() == [(None,)]


def test_embargoed_and_unembargoed_copies_both_load(test_session, test_config, raw_dir):
    """Both copies hold the same workbook. Both load, as two submissions, on
    every table, and their cells are identical."""
    make_stlmt_bundle(raw_dir, time_str=EMBARGOED)
    make_stlmt_bundle(raw_dir, time_str=UNEMBARGOED)
    conn = run(STLMTProcessor, test_session, test_config)

    for table in STLMT_TABLES:
        assert conn.execute(
            f"SELECT submission_id, is_embargoed FROM raw_data.{table} "
            f"GROUP BY ALL ORDER BY 1"
        ).fetchall() == [
            ("STLMT.D259999.T0000000", True),
            ("STLMT.D259999.T1111111", False),
        ], table

    cells = (
        "SELECT row_num, section_code, row_label, column_label, value_text "
        "FROM raw_data.stlmt_table_3 WHERE is_embargoed = {} ORDER BY ALL"
    )
    assert conn.execute(cells.format("TRUE")).fetchall() == conn.execute(
        cells.format("FALSE")
    ).fetchall()


def test_a_reader_can_prefer_the_unembargoed_copy(test_session, test_config, raw_dir):
    """Ordering a performance year's settlements by IS_EMBARGOED (FALSE first)
    picks the unembargoed copy when both exist, and the embargoed one when it
    is all there is."""
    make_stlmt_bundle(raw_dir, year="2024", time_str=EMBARGOED)
    make_stlmt_bundle(raw_dir, year="2025", time_str=EMBARGOED)
    make_stlmt_bundle(raw_dir, year="2025", time_str=UNEMBARGOED)
    conn = run(STLMTProcessor, test_session, test_config)

    assert conn.execute("""
        SELECT performance_year, submission_id
        FROM raw_data.stlmt_table_3
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY aco_id, performance_year
            ORDER BY is_embargoed, submission_id DESC
        ) = 1
        ORDER BY 1
    """).fetchall() == [
        ("2024", "STLMT.D259999.T0000000"),
        ("2025", "STLMT.D259999.T1111111"),
    ]


def test_a_settlement_workbook_outside_a_settlement_bundle_keeps_its_identity(
    test_session, test_config, raw_dir
):
    """The workbook's own name says it is a settlement. Filed outside a STLMT
    bundle directory, it still gets a 'STLMT.' SUBMISSION_ID (from its own
    stamp) rather than a bare stamp a benchmark delivery could share."""
    write_workbook(
        raw_dir / ACO_ID / "2025" / "01" / "misfiled"
        / f"P.{ACO_ID}.ACO.STLMT.Y2025.D259999.T0000000.xlsx",
        stlmt_sheets(),
    )
    conn = run(STLMTProcessor, test_session, test_config)

    assert conn.execute(
        "SELECT DISTINCT submission_id, is_embargoed FROM raw_data.stlmt_table_3"
    ).fetchall() == [("STLMT.D259999.T0000000", True)]


# ===========================================================================
# Lettered rows and enrollment types (what TUVA-159 stages)
# ===========================================================================


def _cells(conn, table, section_code):
    return conn.execute(
        f"SELECT row_label, column_label, value_text FROM raw_data.{table} "
        f"WHERE section_code = ? ORDER BY row_num, column_label",
        [section_code],
    ).fetchall()


def test_table_2_updated_benchmark_rows(test_session, test_config, raw_dir):
    make_stlmt_bundle(raw_dir)
    conn = run(STLMTProcessor, test_session, test_config)

    # [I] carries its value on the lettered row itself.
    assert _cells(conn, "stlmt_table_2", "I") == [
        ("Updated Benchmark ($)", "BY3", "-"),
        ("Updated Benchmark ($)", "Performance Year", UPDATED_BENCHMARK),
    ]
    # [H] heads one row per enrollment type.
    assert [
        (row, value) for row, column, value in _cells(conn, "stlmt_table_2", "H")
        if column == "Performance Year"
    ] == [(t, TYPE_BENCHMARK[t]) for t in TYPES]
    assert conn.execute(
        "SELECT DISTINCT section_label FROM raw_data.stlmt_table_2 WHERE section_code = 'H'"
    ).fetchall() == [("Updated Historical Benchmark Expenditure ($)",)]


def test_table_3_double_letter_codes_and_text_results(
    test_session, test_config, raw_dir
):
    make_stlmt_bundle(raw_dir)
    conn = run(STLMTProcessor, test_session, test_config)

    assert _cells(conn, "stlmt_table_3", "MM") == [
        ("Net Payment ($)", "Performance Year", "-"),
        ("Net Payment ($)", "Result", "-1,234"),
    ]
    assert _cells(conn, "stlmt_table_3", "AA")[1] == ("Final Sharing Rate", "Result", "0.5")
    # 'N/A' is a result, kept verbatim, not read as missing.
    assert _cells(conn, "stlmt_table_3", "P")[1] == ("Savings Realized", "Result", "N/A")
    # Person years: one row per enrollment type under [B].
    assert [
        row for row, column, _ in _cells(conn, "stlmt_table_3", "B")
        if column == "Performance Year"
    ] == list(TYPES)
    # Unbracketed step headers become GROUP_LABEL.
    assert conn.execute(
        "SELECT DISTINCT section_code, group_label FROM raw_data.stlmt_table_3 "
        "WHERE section_code IN ('A', 'P', 'AA', 'MM') AND row_label <> 'Row Unique To Table 3' "
        "ORDER BY 1"
    ).fetchall() == [
        ("A", "Determine Savings"),
        ("AA", "Determine Earned Payment"),
        ("MM", "Determine Earned Payment"),
        ("P", "Determine Savings"),
    ]


def test_table_a1_risk_blocks_by_enrollment_type(test_session, test_config, raw_dir):
    make_stlmt_bundle(raw_dir)
    conn = run(STLMTProcessor, test_session, test_config)

    assert conn.execute(
        "SELECT row_label, column_label, value_text FROM raw_data.stlmt_table_a1 "
        "WHERE section_code = 'B' AND row_label = 'Disabled' ORDER BY column_label"
    ).fetchall() == [
        ("Disabled", "BY3", BY3_RISK_SCORE["Disabled"]),
        ("Disabled", "Performance Year", PY_RISK_SCORE["Disabled"]),
    ]
    assert conn.execute(
        "SELECT COUNT(DISTINCT row_label) FROM raw_data.stlmt_table_a1 "
        "WHERE section_code = 'F'"
    ).fetchone()[0] == len(TYPES)


# ===========================================================================
# The fixture's values follow the CMS calculation (TUVA-165)
#
# TUVA-159 and TUVA-162 assert against these numbers, so they must agree with
# each other the way a real settlement's do, read back through the processor.
# ===========================================================================


def _numbers(conn, table, section_code, column):
    """{row_label: float} for one lettered block and one value column.

    The sheet-marker row ('Row Unique To Table N') is not a figure.
    """
    return {
        row: float(value.replace(",", ""))
        for row, col, value in _cells(conn, table, section_code)
        if col == column
        and value not in ("-", "N/A")
        and not row.startswith("Row Unique To")
    }


def test_table_2_updated_benchmark_is_the_person_year_weighted_type_benchmark(
    test_session, test_config, raw_dir
):
    make_stlmt_bundle(raw_dir)
    conn = run(STLMTProcessor, test_session, test_config)

    person_years = _numbers(conn, "stlmt_table_3", "B", "Performance Year")
    assert set(person_years) == set(TYPES)
    assigned = _numbers(conn, "stlmt_table_3", "A", "Performance Year")["Assigned Beneficiaries"]
    assert sum(person_years.values()) <= assigned

    # Table 2 [A]: each type's share of performance-year person years.
    shares = _numbers(conn, "stlmt_table_2", "A", "Performance Year")
    total = sum(person_years.values())
    assert {t: shares[t] for t in TYPES} == pytest.approx(
        {t: person_years[t] / total for t in TYPES}
    )

    # [I] = sum over types of [A] x [H].
    type_benchmark = _numbers(conn, "stlmt_table_2", "H", "Performance Year")
    updated = _numbers(conn, "stlmt_table_2", "I", "Performance Year")["Updated Benchmark ($)"]
    assert updated == pytest.approx(sum(shares[t] * type_benchmark[t] for t in TYPES))
    # The types differ, so a wrong weighting cannot pass by accident.
    assert len(set(type_benchmark.values())) == len(TYPES)


def test_table_a1_capped_ratio_is_the_score_ratio_below_the_cap(
    test_session, test_config, raw_dir
):
    make_stlmt_bundle(raw_dir)
    conn = run(STLMTProcessor, test_session, test_config)

    by3 = _numbers(conn, "stlmt_table_a1", "B", "BY3")
    py = _numbers(conn, "stlmt_table_a1", "B", "Performance Year")
    capped = _numbers(conn, "stlmt_table_a1", "F", "Performance Year")
    assert set(by3) == set(py) == set(capped) == set(TYPES)
    for t in TYPES:
        ratio = py[t] / by3[t]
        # No demographic growth: the cap is 1.03 and binds for no type.
        assert ratio < 1.03
        assert capped[t] == pytest.approx(ratio)
    assert len(set(capped.values())) == len(TYPES)


# ===========================================================================
# Incremental
# ===========================================================================


def test_second_incremental_run_adds_zero_rows(
    test_session, incremental_config, raw_dir
):
    make_stlmt_bundle(raw_dir)
    conn = run(STLMTProcessor, test_session, incremental_config)
    before = counts(conn)
    assert all(before[t] > 0 for t in STLMT_TABLES)

    run(STLMTProcessor, test_session, incremental_config)
    assert counts(conn) == before


def test_incremental_run_picks_up_the_second_copy(
    test_session, incremental_config, raw_dir
):
    """The unembargoed copy arriving after the embargoed one is appended."""
    make_stlmt_bundle(raw_dir, time_str=EMBARGOED)
    conn = run(STLMTProcessor, test_session, incremental_config)
    first = counts(conn)["stlmt_table_3"]

    make_stlmt_bundle(raw_dir, time_str=UNEMBARGOED)
    run(STLMTProcessor, test_session, incremental_config)
    assert counts(conn)["stlmt_table_3"] == first * 2
