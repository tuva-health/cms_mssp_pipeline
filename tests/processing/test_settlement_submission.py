"""
Settlement submission identity and the embargo flag (TUVA-154).

CMS delivers a final settlement (STLMT) bundle for each performance year. Its
files sit in their own bundle directory, but their D<YYMMDD>.T<NNNNNNN> stamps
can equal a benchmark delivery's. SUBMISSION_ID was the stamp alone, so a
settlement AEXPU on a shared stamp joined that benchmark submission, and the
connector labelled the submission's four workbooks BY0-BY3.

What is tested here, through the processors' output tables:

- settlement workbooks get a SUBMISSION_ID of their own, prefixed 'STLMT.';
  benchmark and quarterly SUBMISSION_IDs keep their bare stamp;
- a settlement AEXPU on a benchmark stamp leaves the benchmark submission with
  only its three benchmark years, so the connector's labelling yields BY1-BY3;
- IS_EMBARGOED is set on settlement rows from the delivery's T stamp
  (T0000000 embargoed, T1111111 unembargoed), falls back to the FinRec notice
  file in the bundle, and is NULL on every non-settlement row;
- embargoed and unembargoed copies of one settlement both load, as two
  submissions.

Every fixture is synthetic.
"""

from pathlib import Path
from typing import Optional

import pytest

from mssp_pipeline.processing.processors.aexpu_processor import AEXPUProcessor
from mssp_pipeline.processing.processors.bnmrk_processor import BNMRKProcessor
from mssp_pipeline.processing.processors.qexpu_processor import QEXPUProcessor

from .conftest import ACO_ID
from .test_aco_workbook_processors import (
    PARAMETERS,
    aexpu_sheets,
    make_bnmrk_bundle,
    make_qexpu_bundle,
    run,
    write_workbook,
)

EMBARGOED = "0000000"
UNEMBARGOED = "1111111"

TYPES = ("ESRD", "Disabled", "Aged/dual", "Aged/non-dual")


# ---------------------------------------------------------------------------
# Synthetic settlement (STLMT) workbook
#
# Sheet names are the real workbook's. The sheet bodies follow the layout of
# the BNMRK report the settlement extends: title rows, a 'Table of Contents'
# link, one header row with two or more value columns, lettered row blocks
# ([A], [B], ... [MM]) that either carry a value themselves or head one row per
# enrollment type, then a 'Note' block of footnote definitions. Every value is
# fake, and each sheet carries a section and a row label naming its own table
# so the table -> sheet mapping is checkable.
# ---------------------------------------------------------------------------


def _type_rows(*values) -> list:
    return [[t, *values] for t in TYPES]


def _per_type_rows(*columns: dict) -> list:
    """One row per enrollment type; each column is a {type: value} mapping."""
    return [[t, *(column[t] for column in columns)] for t in TYPES]


def _all_types(value: str) -> dict:
    return {t: value for t in TYPES}


# The values of Tables 2, 3 and A1 follow the CMS calculation, so TUVA-159 and
# TUVA-162 can assert against them (TUVA-165); every number is made up.
#
# Table 3 [B] performance-year person years, which weight everything else.
PERSON_YEARS = {"ESRD": "20", "Disabled": "80", "Aged/dual": "100", "Aged/non-dual": "800"}
# Table 2 [A] each type's share of them, and [H] its updated benchmark.
PERSON_YEAR_SHARES = {"ESRD": "0.02", "Disabled": "0.08", "Aged/dual": "0.1", "Aged/non-dual": "0.8"}
TYPE_BENCHMARK = {"ESRD": "95000", "Disabled": "14000", "Aged/dual": "21000", "Aged/non-dual": "11000"}
# [I] = sum of [A] x [H] = 1900 + 1120 + 2100 + 8800.
UPDATED_BENCHMARK = "13920"
# Table A1 [B] CMS-HCC scores, and [F] the PY / BY3 ratio (the PY score is
# BY3 x [F], to 7 decimals). Demographic growth is nil, so the cap is 1.03 and
# binds for no type. The BY3 scores are the e2e BNMRK final delivery's Table 1
# [C] BY3 (tests/e2e/fixtures/workbooks/bnmrk_table_1.csv), as in a real
# settlement, where the BY3 side matches the benchmark report.
BY3_RISK_SCORE = {
    "ESRD": "0.8540764", "Disabled": "0.506856", "Aged/dual": "0.5337067", "Aged/non-dual": "0.8598672",
}
PY_RISK_SCORE = {
    "ESRD": "0.8626172", "Disabled": "0.4967189", "Aged/dual": "0.5443808", "Aged/non-dual": "0.8727652",
}
CAPPED_RISK_RATIO = {"ESRD": "1.01", "Disabled": "0.98", "Aged/dual": "1.02", "Aged/non-dual": "1.015"}


def stlmt_matrix(tag: str, columns=("BY3", "Performance Year")) -> list:
    """A settlement data sheet in the BNMRK layout, unique to its table."""
    blanks = [None] * len(columns)
    return [
        [f"Table {tag}", *blanks],
        ["Shared Savings Program Financial Reconciliation Report", *blanks],
        ["Table of Contents", *blanks],
        [f"Table {tag} Calculation", *columns],
        [f"[A] Table {tag} Section", *blanks],
        [f"Row Unique To Table {tag}", *[f"{tag}-{i}" for i in range(len(columns))]],
        *_type_rows(*["1"] * len(columns)),
        ["Note: Numerical values are synthetic.", *blanks],
        [f"[A] Footnote for table {tag}.", *blanks],
    ]


# Table 2: per-type blocks [A]-[H], then the ACO-level updated benchmark [I].
STLMT_TABLE_2 = [
    ["Table 2", None, None],
    ["Shared Savings Program Updated Benchmark", None, None],
    ["Table of Contents", None, None],
    ["Updated Benchmark Calculation", "BY3", "Performance Year"],
    ["[A] Table 2 Section", None, None],
    ["Row Unique To Table 2", "2-0", "2-1"],
    *_per_type_rows(_all_types("-"), PERSON_YEAR_SHARES),
    ["[H] Updated Historical Benchmark Expenditure ($)", None, None],
    *_per_type_rows(_all_types("-"), TYPE_BENCHMARK),
    ["[I] Updated Benchmark ($)", "-", UPDATED_BENCHMARK],
    ["Note: Numerical values are synthetic.", None, None],
    ["[I] Weighted by performance year person years.", None, None],
]

# Table 3: ACO-level lettered rows, a per-type block, double-letter codes, a
# 'N/A' text result and the unbracketed group headers CMS separates steps with.
STLMT_TABLE_3 = [
    ["Table 3", None, None],
    ["Shared Savings Program Shared Savings and Losses", None, None],
    ["Table of Contents", None, None],
    ["Shared Savings/Losses Calculation", "Performance Year", "Result"],
    ["[U] Table 3 Section", None, None],
    ["Row Unique To Table 3", "3-0", "3-1"],
    ["Determine Savings", None, None],
    ["[A] Assigned Beneficiaries", "1050", "-"],
    ["[B] Person Years", None, None],
    *_per_type_rows(PERSON_YEARS, _all_types("-")),
    ["[P] Savings Realized", "-", "N/A"],
    ["Determine Earned Payment", None, None],
    ["[AA] Final Sharing Rate", "-", "0.5"],
    ["[MM] Net Payment ($)", "-", "-1,234"],
    ["Note: Numerical values are synthetic.", None, None],
    ["[MM] Earned payment net of recoupment.", None, None],
]

# Table A1: per-type risk blocks over the BY3 and performance-year columns.
STLMT_TABLE_A1 = [
    ["Table A1", None, None],
    ["Risk Adjustment", None, None],
    ["Table of Contents", None, None],
    ["Risk Calculation", "BY3", "Performance Year"],
    ["[A] Table A1 Section", None, None],
    ["Row Unique To Table A1", "A1-0", "A1-1"],
    ["[B] CMS-HCC Risk Score", None, None],
    *_per_type_rows(BY3_RISK_SCORE, PY_RISK_SCORE),
    ["[F] Capped CMS-HCC Risk Ratio", None, None],
    *_per_type_rows(_all_types("-"), CAPPED_RISK_RATIO),
    ["Note: Numerical values are synthetic.", None, None],
]

# table -> (real sheet name, tag, rows)
STLMT_SHEET_BY_TABLE = {
    "stlmt_table_1": ("Table 1 - Historical Benchmark", "1", stlmt_matrix("1", ("BY1", "BY2", "BY3"))),
    "stlmt_table_1a": ("Table 1A - Regional Adjustment", "1A", stlmt_matrix("1A")),
    "stlmt_table_1b": ("Table 1B - Prior Savings Adj", "1B", stlmt_matrix("1B")),
    "stlmt_table_1c": ("Table 1C - Population Adj", "1C", stlmt_matrix("1C")),
    "stlmt_table_2": ("Table 2 - Updated Benchmark", "2", STLMT_TABLE_2),
    "stlmt_table_3": ("Table 3 - Shared Savings Losses", "3", STLMT_TABLE_3),
    "stlmt_table_a1": ("Table A1 - Risk", "A1", STLMT_TABLE_A1),
    "stlmt_table_a2": ("Table A2 - Renormalization", "A2", stlmt_matrix("A2")),
    "stlmt_table_a3": ("Table A3 - Trend & Update", "A3", stlmt_matrix("A3")),
    "stlmt_table_a4": ("Table A4 - MSR", "A4", stlmt_matrix("A4")),
    "stlmt_table_a5": ("Table A5 - Inputs by Track", "A5", stlmt_matrix("A5")),
    "stlmt_table_a6": ("Table A6 - ACPT", "A6", stlmt_matrix("A6", ("BY3", "PY1", "PY2"))),
}


def stlmt_sheets(with_table_a6: bool = True) -> dict:
    sheets = {
        "Cover": [["Cover page"]],
        "TOC": [["Table of Contents", "Sheet"], ["Table 1", "Table 1 - Historical Benchmark"]],
        "Glossary": [["Term", "Definition"], ["MSR", "Minimum savings rate"]],
        "Parameters": PARAMETERS,
    }
    for table, (sheet_name, _tag, rows) in STLMT_SHEET_BY_TABLE.items():
        if table == "stlmt_table_a6" and not with_table_a6:
            continue
        sheets[sheet_name] = rows
    return sheets


def make_stlmt_bundle(
    raw_dir: Path,
    year: str = "2025",
    code: str = "01",
    date_str: str = "259999",
    time_str: str = UNEMBARGOED,
    file_time_str: Optional[str] = None,
    notices=(),
    aco: str = ACO_ID,
    with_table_a6: bool = True,
    aexpu: Optional[dict] = None,
) -> Path:
    """Build one settlement bundle::

        raw_dir/T0000/{year}/{code}/P.T0000.ACO.STLMT.D{date}.T{time}/
            P.T0000.ACO.STLMT.Y{year}.D{date}.T{file_time}.xlsx  <- STLMTProcessor
            P.T0000.ACO.AEXPU.Y{year}.D{date}.T{file_time}.xlsx  <- AEXPUProcessor
            [<notice> ...]                 <- optional FinRec notice file(s)

    ``file_time_str`` defaults to the bundle's own T stamp. ``aexpu`` replaces
    the minimal AEXPU sheets ({sheet name: rows}).
    """
    bundle = raw_dir / aco / year / code / f"P.{aco}.ACO.STLMT.D{date_str}.T{time_str}"
    file_stamp = f"D{date_str}.T{file_time_str or time_str}"
    write_workbook(
        bundle / f"P.{aco}.ACO.STLMT.Y{year}.{file_stamp}.xlsx",
        stlmt_sheets(with_table_a6),
    )
    write_workbook(
        bundle / f"P.{aco}.ACO.AEXPU.Y{year}.{file_stamp}.xlsx",
        aexpu if aexpu is not None else aexpu_sheets(year),
    )
    for name in notices:
        (bundle / name).write_bytes(b"%PDF-1.4 synthetic notice")
    return bundle


# Mirrors cms_mssp_connector's int_expenditures_annual: the label counts back
# from the newest benchmark year inside one (ACO, PY, SUBMISSION_ID).
CONNECTOR_BY_LABELS = """
    SELECT DISTINCT
        submission_id,
        benchmark_year,
        'BY' || CAST(4 - DENSE_RANK() OVER (
            PARTITION BY aco_id, performance_year, submission_id
            ORDER BY benchmark_year DESC
        ) AS VARCHAR) AS benchmark_year_label
    FROM raw_data.aexpu_table_1
    ORDER BY 1, 2
"""


# ===========================================================================
# Submission identity
# ===========================================================================


def test_settlement_aexpu_on_a_benchmark_stamp_stays_out_of_the_benchmark_submission(
    test_session, test_config, raw_dir
):
    """The reported bug: same PY, same stamp, two bundles -> two submissions.

    With the stamp as the whole identity the benchmark submission held four
    workbooks (2022-2025) and the connector labelled them BY0-BY3. Every label
    must now be BY1-BY3, and the benchmark submission keeps exactly its three
    benchmark years under its unchanged stamp.
    """
    make_bnmrk_bundle(raw_dir, year="2025", date_str="259999", time_str=UNEMBARGOED)
    make_stlmt_bundle(raw_dir, year="2025", date_str="259999", time_str=UNEMBARGOED)
    conn = run(AEXPUProcessor, test_session, test_config)

    assert conn.execute(CONNECTOR_BY_LABELS).fetchall() == [
        ("D259999.T1111111", "2022", "BY1"),
        ("D259999.T1111111", "2023", "BY2"),
        ("D259999.T1111111", "2024", "BY3"),
        ("STLMT.D259999.T1111111", "2025", "BY3"),
    ]


def test_benchmark_and_quarterly_submission_ids_keep_the_bare_stamp(
    test_session, test_config, raw_dir
):
    """Only settlement identities change shape; the connector's benchmark and
    quarterly joins on SUBMISSION_ID see the values they always have."""
    make_bnmrk_bundle(raw_dir, date_str="259999", time_str=UNEMBARGOED)
    make_qexpu_bundle(raw_dir, date_str="269999", time_str="0100000")
    conn = run(BNMRKProcessor, test_session, test_config)
    run(AEXPUProcessor, test_session, test_config)
    run(QEXPUProcessor, test_session, test_config)

    for table, expected in (
        ("bnmrk_table_1", "D259999.T1111111"),
        ("aexpu_table_1", "D259999.T1111111"),
        ("qexpu_table_1", "D269999.T0100000"),
    ):
        assert conn.execute(
            f"SELECT DISTINCT submission_id FROM raw_data.{table}"
        ).fetchall() == [(expected,)], table


def test_settlement_identity_comes_from_the_bundle_stamp(
    test_session, test_config, raw_dir
):
    """The bundle directory is the delivery. A file inside it whose own stamp
    differs still belongs to the bundle's submission, so the embargoed and
    unembargoed copies cannot collapse into one identity."""
    make_stlmt_bundle(raw_dir, time_str=EMBARGOED, file_time_str=UNEMBARGOED)
    conn = run(AEXPUProcessor, test_session, test_config)

    assert conn.execute(
        "SELECT DISTINCT submission_id, is_embargoed FROM raw_data.aexpu_table_1"
    ).fetchall() == [("STLMT.D259999.T0000000", True)]


# ===========================================================================
# Embargo flag
# ===========================================================================


def test_embargoed_and_unembargoed_copies_both_load_and_are_flagged(
    test_session, test_config, raw_dir
):
    """CMS can deliver the settlement twice. Both copies load, as two
    submissions, and the flag tells a reader which one to prefer."""
    make_stlmt_bundle(raw_dir, time_str=EMBARGOED)
    make_stlmt_bundle(raw_dir, time_str=UNEMBARGOED)
    conn = run(AEXPUProcessor, test_session, test_config)

    assert conn.execute(
        "SELECT DISTINCT submission_id, is_embargoed FROM raw_data.aexpu_table_1 "
        "ORDER BY 1"
    ).fetchall() == [
        ("STLMT.D259999.T0000000", True),
        ("STLMT.D259999.T1111111", False),
    ]


@pytest.mark.parametrize(
    "notices,expected",
    [
        ([f"P.{ACO_ID}.ACO.STLMT.Y2025.Embargoed FinRec notice.pdf"], True),
        ([f"P.{ACO_ID}.ACO.STLMT.Y2025.Unembargoed FinRec notice.pdf"], False),
        ([f"P.{ACO_ID}.ACO.STLMT.Y2025.UNEMBARGOED_FINREC_NOTICE.pdf"], False),
        ([], None),
        # Both notices contradict each other: no guess.
        ([f"P.{ACO_ID}.ACO.STLMT.Y2025.Embargoed FinRec notice.pdf",
          f"P.{ACO_ID}.ACO.STLMT.Y2025.Unembargoed FinRec notice.pdf"], None),
        # A file that mentions the embargo but is not a notice says nothing.
        ([f"P.{ACO_ID}.ACO.STLMT.Y2025.embargo-schedule.txt"], None),
    ],
)
def test_a_stamp_without_an_embargo_code_falls_back_to_the_notice_file(
    test_session, test_config, raw_dir, notices, expected
):
    """A T stamp other than T0000000/T1111111 says nothing about the embargo.
    The FinRec notice file in the bundle decides; with no notice the flag is
    NULL rather than a guess."""
    make_stlmt_bundle(raw_dir, time_str="1234567", notices=notices)
    conn = run(AEXPUProcessor, test_session, test_config)

    assert conn.execute(
        "SELECT DISTINCT is_embargoed FROM raw_data.aexpu_table_1"
    ).fetchall() == [(expected,)]


def test_the_stamp_wins_over_a_contradicting_notice_file(
    test_session, test_config, raw_dir
):
    make_stlmt_bundle(
        raw_dir,
        time_str=UNEMBARGOED,
        notices=[f"P.{ACO_ID}.ACO.STLMT.Y2025.Embargoed FinRec notice.pdf"],
    )
    conn = run(AEXPUProcessor, test_session, test_config)

    assert conn.execute(
        "SELECT DISTINCT is_embargoed FROM raw_data.aexpu_table_1"
    ).fetchall() == [(False,)]


def test_non_settlement_rows_carry_a_null_embargo_flag(
    test_session, test_config, raw_dir
):
    """Benchmark deliveries also use T0000000/T1111111 stamps; that must not
    be read as an embargo state outside a settlement bundle."""
    make_bnmrk_bundle(raw_dir, time_str=EMBARGOED)
    make_qexpu_bundle(raw_dir)
    conn = run(BNMRKProcessor, test_session, test_config)
    run(AEXPUProcessor, test_session, test_config)
    run(QEXPUProcessor, test_session, test_config)

    for table in ("bnmrk_table_1", "bnmrk_parameters", "aexpu_table_1", "qexpu_table_1"):
        assert conn.execute(
            f"SELECT DISTINCT is_embargoed FROM raw_data.{table}"
        ).fetchall() == [(None,)], table


def test_a_settlement_bundle_with_no_workbook_rows_still_has_the_flag_column(
    test_session, test_config, raw_dir
):
    """An optional sheet absent from every settlement workbook still produces
    a schema-conformant zero-row table carrying IS_EMBARGOED as BOOLEAN."""
    make_stlmt_bundle(raw_dir, year="2025")  # Y2025: no COVID variant sheets
    conn = run(AEXPUProcessor, test_session, test_config)

    assert conn.execute(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_schema = 'raw_data' AND table_name = 'aexpu_table_1a' "
        "AND column_name = 'is_embargoed'"
    ).fetchall() == [("BOOLEAN",)]
