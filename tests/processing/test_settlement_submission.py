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

import pytest

from mssp_pipeline.processing.processors.aexpu_processor import AEXPUProcessor
from mssp_pipeline.processing.processors.bnmrk_processor import BNMRKProcessor
from mssp_pipeline.processing.processors.qexpu_processor import QEXPUProcessor

from .conftest import ACO_ID
from .test_aco_workbook_processors import (
    aexpu_sheets,
    make_bnmrk_bundle,
    make_qexpu_bundle,
    run,
    write_workbook,
)

EMBARGOED = "0000000"
UNEMBARGOED = "1111111"


def make_stlmt_bundle(
    raw_dir: Path,
    year: str = "2025",
    code: str = "01",
    date_str: str = "259999",
    time_str: str = UNEMBARGOED,
    file_time_str: str = None,
    notice: str = None,
    aco: str = ACO_ID,
) -> Path:
    """Build one settlement bundle holding the performance year's AEXPU::

        raw_dir/T0000/{year}/{code}/P.T0000.ACO.STLMT.D{date}.T{time}/
            P.T0000.ACO.AEXPU.Y{year}.D{date}.T{file_time}.xlsx
            [<notice>]                     <- optional FinRec notice file

    ``file_time_str`` defaults to the bundle's own T stamp.
    """
    bundle = raw_dir / aco / year / code / f"P.{aco}.ACO.STLMT.D{date_str}.T{time_str}"
    file_stamp = f"D{date_str}.T{file_time_str or time_str}"
    write_workbook(
        bundle / f"P.{aco}.ACO.AEXPU.Y{year}.{file_stamp}.xlsx", aexpu_sheets(year)
    )
    if notice:
        (bundle / notice).write_bytes(b"%PDF-1.4 synthetic notice")
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
    "notice,expected",
    [
        ("P.T0000.ACO.STLMT.Y2025.Embargoed FinRec notice.pdf", True),
        ("P.T0000.ACO.STLMT.Y2025.Unembargoed FinRec notice.pdf", False),
        ("P.T0000.ACO.STLMT.Y2025.UNEMBARGOED_FINREC_NOTICE.pdf", False),
        (None, None),
    ],
)
def test_a_stamp_without_an_embargo_code_falls_back_to_the_notice_file(
    test_session, test_config, raw_dir, notice, expected
):
    """A T stamp other than T0000000/T1111111 says nothing about the embargo.
    The FinRec notice file in the bundle decides; with no notice the flag is
    NULL rather than a guess."""
    make_stlmt_bundle(raw_dir, time_str="1234567", notice=notice)
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
        notice="P.T0000.ACO.STLMT.Y2025.Embargoed FinRec notice.pdf",
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
