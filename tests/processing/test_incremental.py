"""
Tests for incremental (FULL_REFRESH=False) deduplication logic.

Each test uses the DuckDB in-memory exporter to verify that:
  - first runs insert all rows
  - subsequent runs with the same file skip re-insertion
  - runs with a new file append only the new rows
  - a full-refresh run replaces existing data
"""

import pytest

from mssp_pipeline.processing.defs.cclf_file_defs import CCLF_FILE_DEFS
from mssp_pipeline.processing.exporters.duckdb_exporter import DuckDBExporter
from mssp_pipeline.processing.processors.cclf_processor import CCLFProcessor
from tests.processing.conftest import make_cclf_file

ZC1_DEF = next(d for d in CCLF_FILE_DEFS if d.filename_pattern == "ZC1")
TABLE = "raw_data.parta_claims_header"

ROW_A = {"CUR_CLM_UNIQ_ID": "0000000000001", "BENE_MBI_ID": "FAKEMBI0001"}
ROW_B = {"CUR_CLM_UNIQ_ID": "0000000000002", "BENE_MBI_ID": "FAKEMBI0002"}


def _run(session, config, full_refresh):
    exporter = DuckDBExporter(schema="raw_data", full_refresh=full_refresh)
    CCLFProcessor(session, exporter, config).run()


def _count(session):
    return session.connection.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]


def test_first_run_inserts_all_rows(test_session, incremental_config, raw_dir):
    """On the first run (table does not yet exist), all rows are inserted."""
    make_cclf_file(raw_dir, ZC1_DEF, [ROW_A, ROW_B])
    _run(test_session, incremental_config, full_refresh=False)

    assert _count(test_session) == 2


def test_second_run_same_file_skips(test_session, incremental_config, raw_dir):
    """A second run with the same source file does not add duplicate rows."""
    make_cclf_file(raw_dir, ZC1_DEF, [ROW_A, ROW_B])
    _run(test_session, incremental_config, full_refresh=False)
    _run(test_session, incremental_config, full_refresh=False)

    assert _count(test_session) == 2


def test_second_run_new_file_appends(test_session, incremental_config, raw_dir):
    """A second run that adds a new source file appends only the new rows."""
    make_cclf_file(raw_dir, ZC1_DEF, [ROW_A], bundle_suffix="_first")
    _run(test_session, incremental_config, full_refresh=False)

    assert _count(test_session) == 1

    # Add a file in a different bundle dir (different FILE_PATH) with one new row.
    make_cclf_file(
        raw_dir, ZC1_DEF, [ROW_B], bundle_suffix="_second", date_str="250201"
    )
    _run(test_session, incremental_config, full_refresh=False)

    assert _count(test_session) == 2


def test_full_refresh_replaces_data(
    test_session, incremental_config, test_config, raw_dir
):
    """A full-refresh run drops and recreates the table from the current source."""
    make_cclf_file(raw_dir, ZC1_DEF, [ROW_A, ROW_B])
    _run(test_session, incremental_config, full_refresh=False)
    assert _count(test_session) == 2

    # Full refresh with the same source: row count stays the same (table is rebuilt).
    _run(test_session, test_config, full_refresh=True)
    assert _count(test_session) == 2
