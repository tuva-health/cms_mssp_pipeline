"""
Tests for BNEXProcessor (Beneficiary Exclusion XML files).

Each test writes a synthetic XML file to tmp_path — no real PHI is used.
A dedicated _BNEXTestSession loads both zipfs and webbed extensions, since
read_xml() (from webbed) is used inside the processor's SQL query.
"""

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest

from mssp_pipeline.processing.exporters.duckdb_exporter import DuckDBExporter
from mssp_pipeline.processing.processors.bnex_processor import BNEXProcessor

ACO_ID = "T0000"
BNEX_TABLE = "raw_data.BENEFICIARY_EXCLUSIONS"


# ---------------------------------------------------------------------------
# Synthetic data builders
# ---------------------------------------------------------------------------


def make_bnex_xml(
    beneficiaries: list,
    performance_year: str = "2025",
    report_month: str = "12",
    header_code: str = "HDR_BNEXC",
    file_creation_date: str = "20260217",
) -> str:
    """Build a BNEX XML string from a list of beneficiary dicts.

    Each dict may contain: mbi, hicn, first_name, middle_name, last_name,
    dob, gender, reasons (list of strings).
    """
    benes_xml = ""
    for b in beneficiaries:
        reasons_xml = "".join(
            f"<BeneExcReason>{r}</BeneExcReason>" for r in b.get("reasons", ["BR"])
        )
        benes_xml += (
            f"<Beneficiary>"
            f"<MBI>{b.get('mbi', '')}</MBI>"
            f"<HICN>{b.get('hicn', '')}</HICN>"
            f"<FirstName>{b.get('first_name', '')}</FirstName>"
            f"<MiddleName>{b.get('middle_name', '')}</MiddleName>"
            f"<LastName>{b.get('last_name', '')}</LastName>"
            f"<DOB>{b.get('dob', '')}</DOB>"
            f"<Gender>{b.get('gender', '')}</Gender>"
            f"<BeneExcReasons>{reasons_xml}</BeneExcReasons>"
            f"</Beneficiary>"
        )
    record_count = len(beneficiaries)
    return (
        f'<?xml version="1.0" encoding="UTF-8" standalone="no"?>'
        f"<PFDCACOBeneData>"
        f"<Header>"
        f"<HeaderCode>{header_code}</HeaderCode>"
        f"<FileCreationDate>{file_creation_date}</FileCreationDate>"
        f"<PerformanceYear>{performance_year}</PerformanceYear>"
        f"<ReportMonth>{report_month}</ReportMonth>"
        f"</Header>"
        f"<Beneficiarys>{benes_xml}</Beneficiarys>"
        f"<Trailer>"
        f"<TrailerCode>TRL_BNEXC</TrailerCode>"
        f"<FileCreationDate>{file_creation_date}</FileCreationDate>"
        f"<RecordCount>{record_count}</RecordCount>"
        f"</Trailer>"
        f"</PFDCACOBeneData>"
    )


def make_xml_file(
    raw_dir: Path,
    content: str,
    date_str: str = "250101",
    release: str = "25",
    time_str: str = "1234567",
) -> Path:
    """Write a BNEX XML file mirroring the production delivery structure:

        raw_dir/T0000/2025/BNEX/P.T0000.BNEX.R{rel}.D{date}.T{time}.xml

    Files are placed in raw_dir/ACO_ID/2025/BNEX/ matching the production layout
    FILE_STORE/ACO_ID/YEAR/TYPE_CODE/P.ACO_ID* that BNEXProcessor now globs.
    """
    year_dir = raw_dir / ACO_ID / "2025" / "BNEX"
    year_dir.mkdir(parents=True, exist_ok=True)
    filename = f"P.{ACO_ID}.BNEX.R{release}.D{date_str}.T{time_str}.xml"
    xml_path = year_dir / filename
    xml_path.write_text(content)
    return xml_path


# ---------------------------------------------------------------------------
# Session and config fixtures
# ---------------------------------------------------------------------------


class _BNEXTestSession:
    """Test session with both zipfs and webbed extensions loaded."""

    def __init__(self):
        self.connection = duckdb.connect(":memory:")
        self.connection.execute(
            "INSTALL zipfs FROM community; LOAD zipfs; SET zipfs_split = '!';"
        )
        self.connection.execute("INSTALL webbed FROM community; LOAD webbed;")

    def close(self):
        self.connection.close()


@pytest.fixture
def bnex_session():
    sess = _BNEXTestSession()
    yield sess
    sess.close()


@pytest.fixture
def raw_dir(tmp_path):
    return tmp_path


@pytest.fixture
def bnex_config(raw_dir):
    return SimpleNamespace(
        ACO_ID=ACO_ID,
        FILE_STORE=str(raw_dir),
        OUTPUT_TYPE="DUCKDB",
        FULL_REFRESH=True,
    )


@pytest.fixture
def bnex_incremental_config(raw_dir):
    return SimpleNamespace(
        ACO_ID=ACO_ID,
        FILE_STORE=str(raw_dir),
        OUTPUT_TYPE="DUCKDB",
        FULL_REFRESH=False,
    )


def _run(session, config):
    exporter = DuckDBExporter(schema="raw_data", full_refresh=config.FULL_REFRESH)
    BNEXProcessor(session, exporter, config).run()


def _fetch(session, col="*"):
    return session.connection.execute(f"SELECT {col} FROM {BNEX_TABLE}").fetchall()


def _count(session):
    return session.connection.execute(f"SELECT COUNT(*) FROM {BNEX_TABLE}").fetchone()[
        0
    ]


# ---------------------------------------------------------------------------
# Beneficiary data extraction
# ---------------------------------------------------------------------------


def test_beneficiary_columns_extracted(bnex_session, bnex_config, raw_dir):
    """All core beneficiary fields are extracted correctly."""
    make_xml_file(
        raw_dir,
        make_bnex_xml(
            [
                {
                    "mbi": "MBI001",
                    "hicn": "HICN001",
                    "first_name": "JOHN",
                    "middle_name": "B",
                    "last_name": "DOE",
                    "dob": "19540604",
                    "gender": "M",
                    "reasons": ["BR"],
                }
            ]
        ),
    )
    _run(bnex_session, bnex_config)

    row = bnex_session.connection.execute(
        f"SELECT MBI, HICN, FIRSTNAME, MIDDLENAME, LASTNAME, DOB, GENDER FROM {BNEX_TABLE}"
    ).fetchone()
    assert row == ("MBI001", "HICN001", "JOHN", "B", "DOE", "19540604", "M")


def test_single_exclusion_reason(bnex_session, bnex_config, raw_dir):
    """A single BeneExcReason is stored as a plain string."""
    make_xml_file(raw_dir, make_bnex_xml([{"mbi": "MBI002", "reasons": ["BR"]}]))
    _run(bnex_session, bnex_config)

    row = bnex_session.connection.execute(
        f"SELECT BENEEXCREASONS FROM {BNEX_TABLE}"
    ).fetchone()
    assert row[0] == "BR"


def test_multiple_exclusion_reasons_comma_joined(bnex_session, bnex_config, raw_dir):
    """Multiple BeneExcReason elements are comma-joined into BENEEXCREASONS."""
    make_xml_file(
        raw_dir, make_bnex_xml([{"mbi": "MBI003", "reasons": ["BR", "DE", "MA"]}])
    )
    _run(bnex_session, bnex_config)

    row = bnex_session.connection.execute(
        f"SELECT BENEEXCREASONS FROM {BNEX_TABLE}"
    ).fetchone()
    assert row[0] == "BR,DE,MA"


def test_multiple_beneficiaries_per_file(bnex_session, bnex_config, raw_dir):
    """All beneficiary rows within a single file are loaded."""
    make_xml_file(
        raw_dir,
        make_bnex_xml(
            [
                {"mbi": "MBI004", "reasons": ["BR"]},
                {"mbi": "MBI005", "reasons": ["DE"]},
                {"mbi": "MBI006", "reasons": ["MA"]},
            ]
        ),
    )
    _run(bnex_session, bnex_config)

    assert _count(bnex_session) == 3


# ---------------------------------------------------------------------------
# Header metadata columns
# ---------------------------------------------------------------------------


def test_header_fields_on_every_row(bnex_session, bnex_config, raw_dir):
    """Header fields are present and correct on every beneficiary row."""
    make_xml_file(
        raw_dir,
        make_bnex_xml(
            [
                {"mbi": "MBI007", "reasons": ["BR"]},
                {"mbi": "MBI008", "reasons": ["DE"]},
            ],
            performance_year="2025",
            report_month="12",
        ),
    )
    _run(bnex_session, bnex_config)

    rows = bnex_session.connection.execute(
        f"SELECT MBI, HEADERCODE, FILECREATIONDATE, PERFORMANCEYEAR, REPORTMONTH "
        f"FROM {BNEX_TABLE} ORDER BY MBI"
    ).fetchall()
    assert len(rows) == 2
    for _mbi, headercode, filecreationdate, perf_year, report_month in rows:
        assert headercode == "HDR_BNEXC"
        assert filecreationdate == "20260217"
        assert perf_year == "2025"
        assert report_month == "12"


# ---------------------------------------------------------------------------
# Standard metadata columns
# ---------------------------------------------------------------------------


def test_file_date_from_filename(bnex_session, bnex_config, raw_dir):
    """FILE_DATE is parsed from the D{YYMMDD} segment in the filename."""
    make_xml_file(
        raw_dir,
        make_bnex_xml([{"mbi": "MBI009", "reasons": ["BR"]}]),
        date_str="250101",
    )
    _run(bnex_session, bnex_config)

    file_date = bnex_session.connection.execute(
        f"SELECT FILE_DATE FROM {BNEX_TABLE}"
    ).fetchone()[0]
    assert file_date == date(2025, 1, 1)


def test_standard_metadata_columns_present(bnex_session, bnex_config, raw_dir):
    """FILE_PATH, DIRECTORY_NAME, and FILE_NAME are populated correctly."""
    make_xml_file(raw_dir, make_bnex_xml([{"mbi": "MBI010", "reasons": ["BR"]}]))
    _run(bnex_session, bnex_config)

    row = bnex_session.connection.execute(
        f"SELECT FILE_PATH, DIRECTORY_NAME, FILE_NAME FROM {BNEX_TABLE}"
    ).fetchone()
    file_path, directory_name, file_name = row

    assert "P.T0000.BNEX" in file_path
    assert file_name.startswith("P.T0000.BNEX")
    assert file_name.endswith(".xml")
    assert f"{ACO_ID}/2025" in directory_name  # year subdir present in path
    assert file_path == f"{directory_name}/{file_name}"


# ---------------------------------------------------------------------------
# Multiple files
# ---------------------------------------------------------------------------


def test_multiple_xml_files_unioned(bnex_session, bnex_config, raw_dir):
    """All matching XML files in FILE_STORE are unioned into a single table."""
    make_xml_file(
        raw_dir,
        make_bnex_xml([{"mbi": "MBI011", "reasons": ["BR"]}]),
        date_str="250101",
        release="01",
    )
    make_xml_file(
        raw_dir,
        make_bnex_xml([{"mbi": "MBI012", "reasons": ["DE"]}]),
        date_str="250201",
        release="02",
    )
    _run(bnex_session, bnex_config)

    assert _count(bnex_session) == 2
    mbis = {r[0] for r in _fetch(bnex_session, "MBI")}
    assert mbis == {"MBI011", "MBI012"}


# ---------------------------------------------------------------------------
# Incremental deduplication
# ---------------------------------------------------------------------------


def test_incremental_second_run_skips(bnex_session, bnex_incremental_config, raw_dir):
    """A second run with the same source file does not add duplicate rows."""
    make_xml_file(raw_dir, make_bnex_xml([{"mbi": "MBI013", "reasons": ["BR"]}]))
    _run(bnex_session, bnex_incremental_config)
    _run(bnex_session, bnex_incremental_config)

    assert _count(bnex_session) == 1


def test_incremental_new_file_appends(bnex_session, bnex_incremental_config, raw_dir):
    """A new XML file is appended; the existing file is not duplicated."""
    make_xml_file(
        raw_dir,
        make_bnex_xml([{"mbi": "MBI014", "reasons": ["BR"]}]),
        date_str="250101",
        release="01",
    )
    _run(bnex_session, bnex_incremental_config)
    assert _count(bnex_session) == 1

    make_xml_file(
        raw_dir,
        make_bnex_xml([{"mbi": "MBI015", "reasons": ["DE"]}]),
        date_str="250201",
        release="02",
    )
    _run(bnex_session, bnex_incremental_config)
    assert _count(bnex_session) == 2
