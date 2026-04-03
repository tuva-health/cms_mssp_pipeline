"""Tests for parser.py using real example CLI output."""

from datetime import date

from mssp_pipeline.integration.parser import parse_list, parse_view, FileEntry

# ---------------------------------------------------------------------------
# Fixtures — copied verbatim from the example output files
# ---------------------------------------------------------------------------

LIST_OUTPUT = """
acoms-cli - ACO-MS CLI


----------------------------------------------------------------------------


List Of Datahub Folders & File Types :


Claim and Claim Line Feed (CCLF) Files
-----Claim and Claim Line Feeds (CCLF) - Weekly, Code 305
-----Claim and Claim Line Feeds (CCLF), Code 113

Monthly Exclusion Files
-----Excluded Beneficiary MBI XREF File, Code 183
-----Beneficiary Data Sharing Exclusion File, Code 114

PC Flex Reports
-----Weekly PPCP Claims Reduction File, Code 295
-----PC Flex Monthly Provider Summary of Claims Reductions Report, Code 286
-----PC Flex Weekly Claims Reductions File, Code 285
-----PC Flex Quality Person-Centered Primary Care Measure (PCPCM) Report, Code 282
-----PC Flex Monthly Payment Report, Code 273

Reports
-----Assignment Report, Code 116
-----CAHPS Survey Results Report, Code 128
-----Opioid Measures Report, Code 119
-----Assignment List Report - Annual, Code 129
-----Financial Reconciliation Package, Code 120
-----Assignment Summary Report - Quarterly, Code 130
-----Medicare Clinical Quality Measures (CQM) Beneficiary List, Code 306
-----Revised Initial Determination Financial Reconciliation Package, Code 281
-----NCBP Data File - Quarterly, Code 132
-----NCBP Data File - Annual, Code 126
-----Adhoc Report, Code 124
-----Assignment Summary Report - Annual, Code 123
-----Other Reports, Code 112
-----ACO Provider/Supplier List Report, Code 134
-----Adhoc Report, Code 152
-----EXPU Report - Quarterly, Code 118
-----Quality Measures Audit Report, Code 127
-----Quality Reconciliation Report, Code 133
-----Web Interface Patient Ranking Report, Code 117
-----EXPU Report - Annual, Code 125
-----Assignment List Report - Quarterly, Code 131
-----Historical Benchmark Report, Code 115

Shadow Bundles Data Files
-----Shadow Bundle Reports for SSP, Code 244


----------------------------------------------------------------------------


Session closed, lasted about 4.3s.
"""

VIEW_OUTPUT = """
 acoms-cli - ACO-MS CLI


----------------------------------------------------------------------------


 Found 12 files.

 List of Files

 1 of 12 - P.C1234.ACO.ZCY25.D250122.T1621240.zip (925.93 MB) Last Updated: 2025-01-24T14:33:13.000Z
 2 of 12 - P.C1234.ACO.ZCY25.D250212.T1202370.zip (50.43 MB) Last Updated: 2025-02-12T22:13:12.000Z
 3 of 12 - P.C1234.ACO.ZCY25.D250513.T1024480.zip (65.68 MB) Last Updated: 2025-05-13T16:00:08.000Z
 4 of 12 - P.C1234.ACO.ZCY25.D250613.T1054110.zip (56.95 MB) Last Updated: 2025-06-13T16:32:13.000Z
 5 of 12 - P.C1234.ACO.ZCY25.D250714.T1543480.zip (67.34 MB) Last Updated: 2025-07-14T20:27:05.000Z
 6 of 12 - P.C1234.ACO.ZCY25.D250820.T1003300.zip (76.25 MB) Last Updated: 2025-08-20T16:27:31.000Z
 7 of 12 - P.C1234.ACO.ZCY25.D250915.T1811080.zip (55.16 MB) Last Updated: 2025-09-15T22:51:40.000Z
 8 of 12 - P.C1234.ACO.ZCY25.D251016.T1156570.zip (62.72 MB) Last Updated: 2025-10-16T16:29:06.000Z
 9 of 12 - P.C1234.ACO.ZCY25.D251117.T1233130.zip (60.58 MB) Last Updated: 2025-11-17T18:56:27.000Z
 10 of 12 - P.C1234.ACO.ZCY25.D251215.T1217140.zip (44.87 MB) Last Updated: 2025-12-15T17:52:29.000Z
 11 of 12 - P.C1234.ACO.ZCY25.D260115.T2347120.zip (285.65 MB) Last Updated: 2026-01-17T01:47:14.000Z
 12 of 12 - P.C1234.ACO.ZCR25.D260219.T1000290.zip (24.35 MB) Last Updated: 2026-02-19T19:31:48.000Z


----------------------------------------------------------------------------


Session closed, lasted about 11.9s.
"""

# ---------------------------------------------------------------------------
# Tests for parse_list
# ---------------------------------------------------------------------------

def test_parse_list_returns_all_codes():
    codes = parse_list(LIST_OUTPUT)
    assert 113 in codes
    assert 116 in codes
    assert 305 in codes
    assert 244 in codes


def test_parse_list_count():
    codes = parse_list(LIST_OUTPUT)
    assert len(codes) == 32


def test_parse_list_empty_output():
    assert parse_list("No files found.") == []


def test_parse_list_no_duplicates_from_multiple_codes_on_same_line():
    # Each line should yield exactly one code
    output = "-----Adhoc Report, Code 124\n-----Adhoc Report, Code 152"
    codes = parse_list(output)
    assert codes == [124, 152]


# ---------------------------------------------------------------------------
# Tests for parse_view
# ---------------------------------------------------------------------------

def test_parse_view_returns_all_entries():
    entries = parse_view(VIEW_OUTPUT)
    assert len(entries) == 12


def test_parse_view_first_entry():
    entries = parse_view(VIEW_OUTPUT)
    first = entries[0]
    assert first.filename == "P.C1234.ACO.ZCY25.D250122.T1621240.zip"
    assert first.last_updated == "2025-01-24T14:33:13.000Z"


def test_parse_view_last_entry():
    entries = parse_view(VIEW_OUTPUT)
    last = entries[-1]
    assert last.filename == "P.C1234.ACO.ZCR25.D260219.T1000290.zip"
    assert last.last_updated == "2026-02-19T19:31:48.000Z"


def test_parse_view_empty_output():
    assert parse_view("No files found.") == []


def test_parse_view_txt_files():
    """Code 183 (XREF) returns .txt files, not .zip."""
    output = """
 1 of 2 - P.C1234.ACO.MBIY25.D250103.T1033540.txt (3.82 KB) Last Updated: 2025-01-07T20:38:22.000Z
 2 of 2 - P.C1234.ACO.MBIY25.D250204.T1357070.txt (9.45 KB) Last Updated: 2025-02-07T19:46:36.000Z

Session closed.
"""
    entries = parse_view(output)
    assert len(entries) == 2
    assert entries[0].filename == "P.C1234.ACO.MBIY25.D250103.T1033540.txt"
    assert entries[0].last_updated == "2025-01-07T20:38:22.000Z"
    assert entries[1].filename == "P.C1234.ACO.MBIY25.D250204.T1357070.txt"


# ---------------------------------------------------------------------------
# Tests for FileEntry.creation_date
# ---------------------------------------------------------------------------

def test_creation_date_parses_correctly():
    entry = FileEntry(
        filename="P.C1234.ACO.ZCY25.D250122.T1621240.zip",
        last_updated="2025-01-24T14:33:13.000Z",
    )
    assert entry.creation_date() == date(2025, 1, 22)


def test_creation_date_december():
    entry = FileEntry(
        filename="P.C1234.ACO.ZCY25.D251215.T1217140.zip",
        last_updated="2025-12-15T17:52:29.000Z",
    )
    assert entry.creation_date() == date(2025, 12, 15)


def test_creation_date_2026():
    entry = FileEntry(
        filename="P.C1234.ACO.ZCY25.D260219.T1000290.zip",
        last_updated="2026-02-19T19:31:48.000Z",
    )
    assert entry.creation_date() == date(2026, 2, 19)


def test_creation_date_8digit_yyyymmdd():
    entry = FileEntry(
        filename="P.C1234.ACO.ZOM25.D20250119.T1234560.zip",
        last_updated="2025-01-20T00:00:00.000Z",
    )
    assert entry.creation_date() == date(2025, 1, 19)
