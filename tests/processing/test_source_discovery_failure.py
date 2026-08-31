"""A listing that cannot run must fail the table, not look like an empty one.

An unreachable prefix, a wrong bucket or expired credentials all make DuckDB's
glob raise. Returning an empty list there is indistinguishable from a delivery
that has not arrived yet, so every table gets skipped and the run still reports
success. These tests pin the difference.
"""
from __future__ import annotations

from types import SimpleNamespace

import duckdb
import pytest

from mssp_pipeline.processing.exceptions import SourceDiscoveryError
from mssp_pipeline.processing.defs.cclf_file_defs import CCLF_FILE_DEFS
from mssp_pipeline.processing.processors.cclf_processor import CCLFProcessor
from mssp_pipeline.processing.processors.bnmrk_processor import BNMRKProcessor


def _processor(execute):
    session = SimpleNamespace(connection=SimpleNamespace(execute=execute))
    config = SimpleNamespace(FILE_STORE="s3://unreachable", ACO_ID="T0000", FULL_REFRESH=False)
    return CCLFProcessor(session, exporter=None, config=config)


def _workbook_processor(execute):
    """A sectioned-workbook processor whose glob listing is driven by `execute`.

    Workbook discovery goes through ACOWorkbookProcessor._find_xlsx_paths, a
    separate glob path from the CCLF/MSSP processors, so it needs its own
    fail-closed coverage.
    """
    session = SimpleNamespace(connection=SimpleNamespace(execute=execute))
    config = SimpleNamespace(FILE_STORE="s3://unreachable", ACO_ID="T0000", FULL_REFRESH=False)
    return BNMRKProcessor(session, exporter=None, config=config)


def test_unlistable_source_raises_rather_than_looking_empty():
    def execute(_sql):
        raise duckdb.IOException("HTTP 403 on s3://unreachable")

    with pytest.raises(SourceDiscoveryError) as excinfo:
        _processor(execute)._list_source_file_paths(CCLF_FILE_DEFS[0])

    message = str(excinfo.value)
    assert "Could not list" in message
    assert "403" in message, "the underlying cause must survive into the message"


def test_a_listing_that_returns_nothing_is_still_empty():
    """The opposite case must keep working: the glob ran, nothing matched."""
    def execute(_sql):
        return SimpleNamespace(fetchall=lambda: [])

    assert _processor(execute)._list_source_file_paths(CCLF_FILE_DEFS[0]) == []


def test_discovery_failure_fails_the_run(capsys):
    """base.run records it as a table failure instead of skipping quietly."""
    def execute(_sql):
        raise duckdb.IOException("HTTP 403 on s3://unreachable")

    processor = _processor(execute)
    with pytest.raises(RuntimeError, match="failed for"):
        processor.run()

    assert "No source files found" not in capsys.readouterr().out


def test_duckdb_raising_on_an_empty_match_is_treated_as_empty():
    """zipfs raises where a local glob returns no rows. Both mean "nothing there".

    This is the branch that keeps a fresh deployment working. Every zip-backed
    processor globs through zipfs, which raises IOException carrying "No files
    found that match the pattern" when a delivery has not arrived yet. Treating
    that as a discovery failure would fail the run on an empty file store.
    """
    def execute(_sql):
        raise duckdb.IOException(
            'IO Error: No files found that match the pattern "/store/T0000/*/*/x"'
        )

    assert _processor(execute)._list_source_file_paths(CCLF_FILE_DEFS[0]) == []


def test_workbook_discovery_unlistable_source_raises_rather_than_looking_empty():
    """The workbook glob path must fail closed exactly like every other processor.

    aco_workbook._find_xlsx_paths is a distinct listing path; before this it
    swallowed every IOException and returned [], so a 403 on the workbook prefix
    looked like "no benchmark deliveries yet" and the run reported success.
    """
    def execute(_sql):
        raise duckdb.IOException("HTTP 403 on s3://unreachable")

    with pytest.raises(SourceDiscoveryError) as excinfo:
        _workbook_processor(execute)._find_xlsx_paths()

    message = str(excinfo.value)
    assert "Could not list" in message
    assert "403" in message, "the underlying cause must survive into the message"


def test_workbook_discovery_returning_nothing_is_still_empty():
    """A workbook glob that ran and matched nothing is a real empty, not a failure."""
    def execute(_sql):
        return SimpleNamespace(fetchall=lambda: [])

    assert _workbook_processor(execute)._find_xlsx_paths() == []


def test_workbook_discovery_empty_glob_exception_is_treated_as_empty():
    """zipfs/remote 'no files match' means the prefix is empty, not unreachable."""
    def execute(_sql):
        raise duckdb.IOException(
            'IO Error: No files found that match the pattern "/store/T0000/*/*/*/x"'
        )

    assert _workbook_processor(execute)._find_xlsx_paths() == []


def test_the_two_cases_are_told_apart_by_the_message_only():
    """Guard the discriminator itself, since DuckDB gives no other signal."""
    from mssp_pipeline.processing.exceptions import is_empty_glob

    assert is_empty_glob(duckdb.IOException('No files found that match the pattern "x"'))
    assert is_empty_glob(duckdb.IOException('io error: NO FILES FOUND THAT MATCH THE PATTERN'))
    assert not is_empty_glob(duckdb.IOException("HTTP 403 Forbidden"))
    assert not is_empty_glob(duckdb.IOException("Could not establish connection"))
    assert not is_empty_glob(duckdb.IOException("No such file or directory"))
