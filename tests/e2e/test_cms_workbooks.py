"""The e2e workbooks parse back into exactly the connector's seed rows (TUVA-165).

Seam: the processors' output tables. ``cms_workbooks.render_delivery`` writes
xlsx files from the fixture CSVs (cms_mssp_connector's workbook seeds); the
real BNMRK / AEXPU / QEXPU processors read them; every output row must equal a
fixture row, column for column, and every fixture row must come back. Only the
file-store root differs (the fixtures are rooted at ``/synthetic/store``).
"""

import csv

import pytest

from mssp_pipeline.processing.processors.aexpu_processor import AEXPUProcessor
from mssp_pipeline.processing.processors.bnmrk_processor import BNMRKProcessor
from mssp_pipeline.processing.processors.qexpu_processor import QEXPUProcessor
from tests.e2e import cms_workbooks
from tests.processing.conftest import _TestSession
from tests.processing.test_aco_workbook_processors import config_for, run

SEED_COLUMNS = [
    "row_num", "group_label", "section_code", "section_label", "row_label",
    "column_group_label", "column_label", "value_text", "file_path",
    "directory_name", "file_name", "file_date", "aco_id", "performance_year",
    "benchmark_year", "submission_id", "period",
]


@pytest.fixture(scope="module")
def parsed(tmp_path_factory):
    store = tmp_path_factory.mktemp("store")
    cms_workbooks.render_delivery(store)
    session = _TestSession()
    config = config_for(store, aco=cms_workbooks.E2E_ACO_ID)
    for processor in (BNMRKProcessor, AEXPUProcessor, QEXPUProcessor):
        run(processor, session, config)
    yield store, session.connection
    session.close()


def fixture_rows(table):
    with open(cms_workbooks.FIXTURES / f"{table}.csv", newline="", encoding="utf-8") as fh:
        return sorted(tuple(r[c] for c in SEED_COLUMNS) for r in csv.DictReader(fh))


def output_rows(conn, store, table):
    root = str(store).replace("\\", "/") + "/"
    cols = ", ".join(f"COALESCE(CAST({c} AS VARCHAR), '')" for c in SEED_COLUMNS)
    rows = conn.execute(f"SELECT {cols} FROM raw_data.{table}").fetchall()
    out = []
    for row in rows:
        row = list(row)
        for i, name in enumerate(SEED_COLUMNS):
            if name in ("file_path", "directory_name") and row[i].startswith(root):
                row[i] = cms_workbooks.FIXTURE_STORE + row[i][len(root):]
        out.append(tuple(row))
    return sorted(out)


@pytest.mark.parametrize("table", sorted(cms_workbooks.SHEET_NAME))
def test_rendered_workbooks_parse_back_into_the_seed_rows(parsed, table):
    store, conn = parsed
    expected = fixture_rows(table)
    actual = output_rows(conn, store, table)
    missing = sorted(set(expected) - set(actual))[:5]
    extra = sorted(set(actual) - set(expected))[:5]
    assert actual == expected, f"{table}: missing {missing}, unexpected {extra}"


def test_the_fixtures_carry_the_e2e_aco_only():
    workbooks = cms_workbooks.load_cells()
    assert workbooks
    assert all(rel.startswith(f"{cms_workbooks.E2E_ACO_ID}/") for rel in workbooks)
