"""
STLMTProcessor: the final settlement (financial reconciliation) workbook (TUVA-158).

The settlement bundle's 'P.{ACO}.ACO.STLMT.Y{PY}.D...T....xlsx' workbook holds
CMS's final result for a performance year. Its 'Table *' sheets and its
Parameters tab load into STLMT_* cell-grain tables through the shared
sectioned-sheet grammar; Cover, TOC and Glossary do not.

Tested through the processor's output tables (raw_data.stlmt_*), against a
synthetic workbook in the real CMS layout (stlmt_workbook.py):

- each STLMT table holds its own sheet, every sheet is found (a short sheet
  behind a low cover used to vanish), and a missing required sheet fails;
- discovery keys on the '.STLMT.Y' filename token and ignores the bundle's
  other workbooks;
- PERFORMANCE_YEAR, SUBMISSION_ID and IS_EMBARGOED identify the delivery, and
  the embargoed and unembargoed copies both load with the unembargoed one
  identifiable as preferred;
- each table keeps the real structure through the layout grammar
  (BLOCK_LABEL, ROW_KIND, COLUMN_CODE): Table 3's [A]-[UU], Table A1's
  lettered columns, Table 2's total row, Table A2's blocks, Table A4's grid;
- the numbers read back agree across tables the way CMS computes them.

Every fixture is synthetic.
"""

from datetime import date

import pytest

from mssp_pipeline.processing.exporters.duckdb_exporter import DuckDBExporter
from mssp_pipeline.processing.processors.stlmt_processor import STLMTProcessor

from . import stlmt_workbook as wb
from .conftest import ACO_ID
from .stlmt_workbook import STLMT_SHEET_BY_TABLE, TYPES, stlmt_sheets
from .test_aco_workbook_processors import (
    config_for,
    counts,
    make_bnmrk_bundle,
    run,
    write_workbook,
)
from .test_settlement_submission import EMBARGOED, UNEMBARGOED, make_stlmt_bundle

STLMT_TABLES = set(STLMT_SHEET_BY_TABLE) | {"stlmt_parameters"}


# ===========================================================================
# Table -> sheet mapping
# ===========================================================================


# A cell only its own sheet has: (table, SQL predicate).
SIGNATURES = {
    "stlmt_table_1": "section_code = 'A1' AND section_label = 'Assigned Beneficiaries'",
    "stlmt_table_1a": "section_code = 'N' AND column_label = 'Regional Adjustment'",
    "stlmt_table_1b": "section_label = 'Prior Savings Adjustment ($)'",
    "stlmt_table_1c": "section_label = 'Population Adjustment ($)'",
    "stlmt_table_2": "section_label = 'Updated Benchmark ($)'",
    "stlmt_table_3": "section_code = 'UU'",
    "stlmt_table_a1": "column_code = 'I' AND column_label = 'Final Risk Ratio (Capped)'",
    "stlmt_table_a2": f"block_label = '{wb.A2_BLOCKS[1]}'",
    "stlmt_table_a3": "section_label = 'Three-Way Blended Update Factors'",
    "stlmt_table_a4": "column_label = 'MSR Range: High'",
    "stlmt_table_a5": "column_label = 'ENHANCED Track'",
    "stlmt_table_a6": "section_label = 'ACPT'",
}


@pytest.fixture
def stlmt(test_session, test_config, raw_dir):
    make_stlmt_bundle(raw_dir)
    return run(STLMTProcessor, test_session, test_config)


@pytest.mark.parametrize("table", sorted(SIGNATURES))
def test_each_stlmt_table_holds_its_own_sheet(stlmt, table):
    """A pattern pointing at the wrong sheet fails here: 'Table 1 - %' must not
    take Table 1A-1C, and 'Table A1 - %' must not take Table 1."""
    for other in SIGNATURES:
        found = stlmt.execute(
            f"SELECT COUNT(*) FROM raw_data.{other} WHERE {SIGNATURES[table]}"
        ).fetchone()[0]
        assert (found > 0) == (other == table), (table, other)


def test_every_sheet_loads_rows(stlmt):
    """Tables 1C, A1 and A5 are short sheets behind a cover that starts low,
    the shape that made a combined read_sheets() drop them (TUVA-167)."""
    assert all(n > 0 for n in counts(stlmt).values()), counts(stlmt)


def test_a_missing_required_sheet_fails_the_run(test_session, test_config, raw_dir):
    sheets = stlmt_sheets()
    del sheets["Table A1 - Risk"]
    write_workbook(
        raw_dir / ACO_ID / "2025" / "01" / f"P.{ACO_ID}.ACO.STLMT.D259999.T1111111"
        / f"P.{ACO_ID}.ACO.STLMT.Y2025.D259999.T1111111.xlsx",
        sheets,
    )
    with pytest.raises(RuntimeError, match="STLMT_TABLE_A1"):
        run(STLMTProcessor, test_session, test_config)


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


def test_notes_rows_are_dropped(stlmt):
    assert stlmt.execute(
        "SELECT COUNT(*) FROM raw_data.stlmt_table_a5 WHERE row_label LIKE 'Note%'"
    ).fetchone()[0] == 0


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
# The real layout, through the layout grammar (TUVA-167)
# ===========================================================================

DASH = "–"

# Table 3's codes, in sheet order, from the CMS template.
TABLE_3_CODES = [
    "A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O",
    "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z", "AA", "BB", "CC",
    "DD", "EE", "FF", "GG", "HH", "II", "JJ", "KK", "LL", "MM", "NN", "OO",
    "PP", "QQ", "RR", "SS", "TT", "UU",
]


def _rows(conn, table, where="TRUE", columns="*"):
    return conn.execute(
        f"SELECT {columns} FROM raw_data.{table} WHERE {where} ORDER BY row_num, column_label"
    ).fetchall()


def test_table_3_loads_every_code_in_one_value_column(stlmt):
    codes = [
        r[0] for r in stlmt.execute(
            "SELECT section_code FROM raw_data.stlmt_table_3 "
            "GROUP BY section_code ORDER BY MIN(row_num)"
        ).fetchall()
    ]
    assert codes == TABLE_3_CODES
    assert _rows(stlmt, "stlmt_table_3", columns="DISTINCT column_label") == [
        ("Performance Year",)
    ]
    # [C] and [D] are per type; every other code is one value on its own row.
    assert stlmt.execute(
        "SELECT section_code, row_kind, COUNT(*) FROM raw_data.stlmt_table_3 "
        "WHERE section_code IN ('B', 'C', 'D') GROUP BY ALL ORDER BY 1"
    ).fetchall() == [("B", "CODE", 1), ("C", "DETAIL", 4), ("D", "DETAIL", 4)]
    assert stlmt.execute(
        "SELECT COUNT(*) FROM raw_data.stlmt_table_3 WHERE row_kind = 'CODE'"
    ).fetchone()[0] == len(TABLE_3_CODES) - 2


def test_table_3_keeps_text_dashes_and_e_notation_verbatim(stlmt):
    value = dict(stlmt.execute(
        "SELECT section_code, value_text FROM raw_data.stlmt_table_3 WHERE row_kind = 'CODE'"
    ).fetchall())
    assert (value["P"], value["Z"], value["AA"], value["BB"]) == ("Savings", "Y", "NA", "N")
    assert value["M"] == DASH and value["UU"] == DASH
    assert value["OO"] == "5.0E7"
    assert stlmt.execute(
        "SELECT DISTINCT group_label FROM raw_data.stlmt_table_3 WHERE section_code = 'CC'"
    ).fetchall() == [("Sharing and Losses Rate Calculations",)]


def test_table_a1_letters_are_column_codes_with_clean_labels(stlmt):
    assert stlmt.execute(
        "SELECT column_code, column_label FROM raw_data.stlmt_table_a1 "
        "GROUP BY ALL ORDER BY 1"
    ).fetchall() == [
        ("A", "Risk Score Weights"),
        ("B", "BY3 CMS-HCC Risk Score"),
        ("C", "PY CMS-HCC Risk Score"),
        ("D", "CMS-HCC Risk Ratio (Uncapped)"),
        ("E", "BY3 Demographic Risk Score"),
        ("F", "PY Demographic Risk Score"),
        ("G", "Demographic Risk Ratio"),
        ("H", "Aggregate Cap"),
        ("I", "Final Risk Ratio (Capped)"),
    ]
    assert stlmt.execute(
        "SELECT DISTINCT section_code, row_kind FROM raw_data.stlmt_table_a1"
    ).fetchall() == [(None, "DETAIL")]
    # One row per type, then the weighted average, filled only under [D] and [G].
    assert [
        r[0] for r in stlmt.execute(
            "SELECT row_label FROM raw_data.stlmt_table_a1 GROUP BY 1 ORDER BY MIN(row_num)"
        ).fetchall()
    ] == [*TYPES, "Weighted Average"]
    assert stlmt.execute(
        "SELECT column_code FROM raw_data.stlmt_table_a1 "
        f"WHERE row_label = 'Weighted Average' AND value_text <> '{DASH}' ORDER BY 1"
    ).fetchall() == [("D",), ("G",)]


def test_table_2_drops_sub_header_text_and_marks_the_total_row(stlmt):
    assert stlmt.execute(
        "SELECT COUNT(*) FROM raw_data.stlmt_table_2 "
        "WHERE value_text IN ('3-Year Average Benchmark', 'BY3')"
    ).fetchone()[0] == 0
    assert _rows(stlmt, "stlmt_table_2", columns="DISTINCT column_label") == [
        ("Benchmark Period",), ("Performance Year",),
    ]
    assert stlmt.execute(
        "SELECT section_code, row_label, column_label FROM raw_data.stlmt_table_2 "
        f"WHERE row_kind = 'TOTAL' AND value_text <> '{DASH}'"
    ).fetchall() == [
        ("A", "Historical Benchmark Expenditures After Applying Adjustment", "Benchmark Period")
    ]
    # [B] carries BY3 and PY on the same per-type row.
    assert stlmt.execute(
        "SELECT row_label, COUNT(*) FROM raw_data.stlmt_table_2 "
        f"WHERE section_code = 'B' AND value_text <> '{DASH}' GROUP BY 1 ORDER BY MIN(row_num)"
    ).fetchall() == [(t, 2) for t in TYPES]
    assert stlmt.execute(
        "SELECT row_kind, COUNT(*) FROM raw_data.stlmt_table_2 WHERE section_code = 'I' GROUP BY 1"
    ).fetchall() == [("CODE", 2)]


def test_table_a2_blocks_are_labelled(stlmt):
    assert stlmt.execute(
        "SELECT block_label, COUNT(DISTINCT row_label), COUNT(*) "
        "FROM raw_data.stlmt_table_a2 GROUP BY 1 ORDER BY MIN(row_num)"
    ).fetchall() == [(wb.A2_BLOCKS[0], 4, 16), (wb.A2_BLOCKS[1], 4, 16)]
    assert stlmt.execute(
        "SELECT COUNT(*) FROM raw_data.stlmt_table_a2 WHERE row_label = 'Medicare Enrollment Type'"
    ).fetchone()[0] == 0


def test_table_a4_is_a_grid_keyed_by_its_low_bound(stlmt):
    assert stlmt.execute(
        "SELECT row_label, column_label, value_text FROM raw_data.stlmt_table_a4 "
        "WHERE row_label = '10000' ORDER BY column_label"
    ).fetchall() == [
        ("10000", "MSR Range: High", "0.027"),
        ("10000", "MSR Range: Low", "0.03"),
        ("10000", "Number of Assigned Beneficiaries Range: High", "14999"),
        ("10000", "Number of Assigned Beneficiaries Range: Low", "10000"),
    ]
    assert stlmt.execute(
        "SELECT COUNT(DISTINCT row_label) FROM raw_data.stlmt_table_a4"
    ).fetchone()[0] == len(wb.MSR_GRID)


def test_table_1b_keeps_its_blank_lettered_rows(stlmt):
    assert stlmt.execute(
        "SELECT section_code, COUNT(*), COUNT(value_text) FROM raw_data.stlmt_table_1b "
        "WHERE section_code IN ('E', 'F') GROUP BY 1 ORDER BY 1"
    ).fetchall() == [("E", 4, 0), ("F", 4, 0)]


def test_table_1_drops_the_excel_spacer_column(stlmt):
    assert sorted(_rows(stlmt, "stlmt_table_1", columns="DISTINCT column_label")) == [
        ("BY1",), ("BY2",), ("BY3",), ("Benchmark",),
    ]


def test_parameters_tell_repeated_keys_apart_by_group(stlmt):
    assert stlmt.execute(
        "SELECT group_label, value_text FROM raw_data.stlmt_parameters "
        "WHERE row_label = 'Benchmark Year 3 (BY3)' ORDER BY row_num"
    ).fetchall() == [
        ("Claims-Based Beneficiary Assignment Window", "01/01/2024 - 12/31/2024"),
        ("Expenditure & Risk Score Period", "01/01/2024 - 12/31/2024"),
        ("Benchmark Year Weights", "0.6"),
    ]
    assert stlmt.execute(
        "SELECT value_text FROM raw_data.stlmt_parameters WHERE row_label = 'ACO Track'"
    ).fetchone()[0] == "BASIC Level A"


# ===========================================================================
# The fixture's numbers follow the CMS calculation (TUVA-165, TUVA-167)
#
# TUVA-168 asserts against these numbers, so they must agree with each other
# the way a real settlement's do, read back through the processor.
# ===========================================================================


def _num(value: str) -> float:
    return float(value.replace(",", ""))


def _by_type(conn, table, code, column):
    """{type: float} for one lettered block and one value column."""
    rows = conn.execute(
        f"SELECT row_label, value_text FROM raw_data.{table} "
        "WHERE section_code = ? AND column_label = ? AND row_kind = 'DETAIL'",
        [code, column],
    ).fetchall()
    return {row: _num(value) for row, value in rows}


def _single(conn, table, code, column="Performance Year"):
    (value,) = conn.execute(
        f"SELECT value_text FROM raw_data.{table} "
        "WHERE section_code = ? AND column_label = ? AND row_kind IN ('CODE', 'TOTAL')",
        [code, column],
    ).fetchone()
    return _num(value)


def _a1(conn, code):
    return {
        row: _num(value) for row, value in conn.execute(
            "SELECT row_label, value_text FROM raw_data.stlmt_table_a1 "
            "WHERE column_code = ? AND row_label <> 'Weighted Average'",
            [code],
        ).fetchall()
    }


def test_table_a1_ratios_follow_the_scores_and_the_cap(stlmt):
    a, b, c, d = (_a1(stlmt, k) for k in "ABCD")
    e, f, g, h, i = (_a1(stlmt, k) for k in "EFGHI")
    assert set(b) == set(TYPES)
    assert sum(a.values()) == pytest.approx(1)
    for t in TYPES:
        assert d[t] == pytest.approx(c[t] / b[t])
        assert g[t] == pytest.approx(f[t] / e[t])
    weighted_d = sum(a[t] * d[t] for t in TYPES)
    weighted_g = sum(a[t] * g[t] for t in TYPES)
    assert h == pytest.approx({t: weighted_g + 0.03 for t in TYPES})
    # The cap does not bind, so the final ratio is the uncapped one.
    assert weighted_d < min(h.values())
    assert i == pytest.approx(d)
    assert len(set(i.values())) == len(TYPES)


def test_table_2_follows_tables_1_a1_a3_and_3(stlmt):
    hb = _by_type(stlmt, "stlmt_table_2", "A", "Benchmark Period")
    by3 = _by_type(stlmt, "stlmt_table_2", "B", "Benchmark Period")
    py = _by_type(stlmt, "stlmt_table_2", "B", "Performance Year")
    c, d, e, f, g, h = (
        _by_type(stlmt, "stlmt_table_2", k, "Performance Year") for k in "CDEFGH"
    )
    assert set(hb) == set(TYPES)
    assert hb == pytest.approx(_by_type(stlmt, "stlmt_table_1", "L", "Benchmark"))
    assert _single(stlmt, "stlmt_table_2", "A", "Benchmark Period") == pytest.approx(
        _single(stlmt, "stlmt_table_1", "M", "Benchmark")
    )
    assert by3 == pytest.approx(_a1(stlmt, "B"))
    assert py == pytest.approx(_a1(stlmt, "C"))
    assert c == pytest.approx(_a1(stlmt, "I"))
    assert e == pytest.approx(_by_type(stlmt, "stlmt_table_a3", "G", "PY"))
    assert f == pytest.approx(_by_type(stlmt, "stlmt_table_a3", "I", "PY"))
    assert h == pytest.approx(_by_type(stlmt, "stlmt_table_3", "D", "Performance Year"))
    for t in TYPES:
        assert d[t] == pytest.approx(hb[t] * c[t])
        assert g[t] == pytest.approx(d[t] * f[t])
    updated = _single(stlmt, "stlmt_table_2", "I")
    assert updated == pytest.approx(sum(g[t] * h[t] for t in TYPES))
    # TUVA-165's updated benchmark is kept.
    assert updated == pytest.approx(13920)


def test_table_3_follows_the_shared_savings_calculation(stlmt):
    v = {
        code: _single(stlmt, "stlmt_table_3", code)
        for code in ("A", "B", "E", "F", "G", "H", "I", "J", "K", "L", "N", "X",
                     "CC", "EE", "FF", "GG", "HH", "JJ", "KK", "LL", "MM")
    }
    per_capita = _by_type(stlmt, "stlmt_table_3", "C", "Performance Year")
    shares = _by_type(stlmt, "stlmt_table_3", "D", "Performance Year")
    assert sum(shares.values()) == pytest.approx(1)
    assert v["E"] == pytest.approx(sum(per_capita[t] * shares[t] for t in TYPES))
    assert v["F"] == pytest.approx(_single(stlmt, "stlmt_table_2", "I"))
    assert v["G"] == pytest.approx(v["E"] * v["B"])
    assert v["H"] == pytest.approx(v["F"] * v["B"])
    assert v["I"] == pytest.approx(v["H"] - v["G"])
    assert (v["J"], v["K"]) == pytest.approx((max(v["I"], 0), min(v["I"], 0)))
    assert v["N"] == pytest.approx(v["L"] * v["H"])
    # The MSR is Table A4's sliding scale at [A] beneficiaries.
    low, high, msr_low, msr_high = next(
        (lo, hi, ml, mh) for lo, hi, ml, mh in wb.MSR_GRID
        if hi.isdigit() and int(lo) <= v["A"] <= int(hi)
    )
    span = (v["A"] - int(low)) / (int(high) + 1 - int(low))
    assert v["L"] == pytest.approx(float(msr_low) + span * (float(msr_high) - float(msr_low)))
    # The quality standard is met, so the final rate is Table A5's maximum.
    assert v["X"] == _num(stlmt.execute(
        "SELECT value_text FROM raw_data.stlmt_table_a5 "
        "WHERE row_label LIKE 'Maximum Sharing Rate%' AND column_label = 'BASIC Track Level A'"
    ).fetchone()[0])
    assert v["CC"] == v["X"]
    assert v["EE"] == pytest.approx(v["J"] * v["CC"])
    assert v["GG"] == pytest.approx(0.02 * v["EE"])
    assert v["HH"] == pytest.approx(min(v["EE"] - v["GG"], v["FF"]))
    assert v["LL"] == pytest.approx(v["JJ"] - v["KK"])
    assert v["MM"] == pytest.approx(v["HH"] - v["KK"])


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
