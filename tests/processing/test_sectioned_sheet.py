"""
Tests for SectionedSheetProcessor — the generic reader for CMS "sectioned"
xlsx data sheets (BNMRK / AEXPU / QEXPU).

Every fixture is a small synthetic xlsx built with openpyxl in tmp_path.
No real ACO data is used anywhere in this suite.

Each test isolates one rule of the sheet grammar: title rows, header-row
detection, the two-level section hierarchy, unpivoting, label normalization
and the notes/footnote block.
"""

from datetime import date
from types import SimpleNamespace

import openpyxl
import pytest

from mssp_pipeline.processing.defs.sectioned_sheet_defs import SheetDef
from mssp_pipeline.processing.exporters.duckdb_exporter import DuckDBExporter
from mssp_pipeline.processing.processors.sectioned_sheet import SectionedSheetProcessor

# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def make_xlsx(tmp_path, sheets: dict, name: str = "report.xlsx"):
    """Write an xlsx with {sheet_name: [[row], ...]} and return its path string.

    All rows — titles, header, sections, data, notes — are passed as plain data
    because the processor reads with header=false.
    """
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for sheet_name, rows in sheets.items():
        ws = wb.create_sheet(sheet_name)
        for row in rows:
            ws.append(row)
    path = tmp_path / name
    wb.save(path)
    return str(path)


class _Processor(SectionedSheetProcessor):
    """Concrete SectionedSheetProcessor with an explicit list of source files."""

    def __init__(self, session, exporter, config, sheet_defs, paths):
        super().__init__(session, exporter, config)
        self.SHEET_DEFS = sheet_defs
        self._paths = paths

    def _list_source_file_paths(self, file_def):
        return [(p.replace("\\", "/"), p) for p in self._paths]


def run_processor(session, sheet_defs, paths, config=None, processor_cls=_Processor):
    """Run the processor into DuckDB's raw_data schema and return the connection."""
    config = config or SimpleNamespace(
        ACO_ID="T0000", FULL_REFRESH=True, PROCESS_BATCH_SIZE_DEFAULT=25
    )
    exporter = DuckDBExporter(schema="raw_data", full_refresh=True)
    processor_cls(session, exporter, config, sheet_defs, paths).run()
    return session.connection


def defs_for(pattern="Table 1 - %", table="SHEET_TABLE", **kwargs):
    return [SheetDef(table_name=table, sheet_pattern=pattern, **kwargs)]


# ---------------------------------------------------------------------------
# Shared synthetic sheet
# ---------------------------------------------------------------------------

# Mirrors the real grammar: 4 title rows, a header row, an unbracketed section,
# a bracketed section carrying its own values, plain data rows, then notes.
BASIC_SHEET = [
    ["Table 1", None, None],                          # 1  title
    ["Shared Savings Program Report", None, None],    # 2  title
    ["T0000, TEST ACO", None, None],                  # 3  title
    ["Table of Contents", None, None],                # 4  title
    ["Benchmark Calculation", "BY1", "BY2"],          # 5  HEADER
    ["Assigned Beneficiaries", None, None],           # 6  section (unbracketed)
    ["[A1] Assigned Beneficiaries", "5780", "5902"],  # 7  section WITH values
    ["[A2] Person Years", "5622", "5778"],            # 8  section WITH values
    ["ESRD", "-", "42"],                              # 9  data
    ["Trended Expenditures", None, None],             # 10 section (unbracketed)
    ["[B] Per Capita Expenditures ($)", None, None],  # 11 section (bracketed)
    ["ESRD", "144209", "153997"],                     # 12 data
    ["Note: values are rounded.", None, None],        # 13 notes
    ["[1]A footnote definition.", None, None],        # 14 footnote
]


# ---------------------------------------------------------------------------
# Title rows
# ---------------------------------------------------------------------------


def test_title_rows_are_dropped(test_session, tmp_path):
    """Rows above the header row are titles and never reach the output."""
    path = make_xlsx(tmp_path, {"Table 1 - Benchmark": BASIC_SHEET})
    conn = run_processor(test_session, defs_for(), [path])

    labels = {r[0] for r in conn.execute(
        "SELECT row_label FROM raw_data.sheet_table "
        "UNION ALL SELECT section_label FROM raw_data.sheet_table "
        "UNION ALL SELECT group_label FROM raw_data.sheet_table"
    ).fetchall()}
    for title in ("Table 1", "Shared Savings Program Report", "T0000, TEST ACO",
                  "Table of Contents", "Benchmark Calculation"):
        assert title not in labels

    assert conn.execute(
        "SELECT MIN(row_num) FROM raw_data.sheet_table"
    ).fetchone()[0] == 7


def test_output_schema_column_order(test_session, tmp_path):
    """Column names and order are fixed: grammar columns, then file metadata."""
    path = make_xlsx(tmp_path, {"Table 1 - Benchmark": BASIC_SHEET})
    conn = run_processor(test_session, defs_for(), [path])

    cols = [r[0] for r in conn.execute("DESCRIBE raw_data.sheet_table").fetchall()]
    assert cols == [
        "row_num", "group_label", "section_code", "section_label",
        "row_label", "column_group_label", "column_label", "value_text",
        "file_path", "directory_name", "file_name", "file_date",
    ]


# ---------------------------------------------------------------------------
# Header row detection
# ---------------------------------------------------------------------------


def test_header_detection_ignores_single_cell_decorative_row(test_session, tmp_path):
    """A row with only ONE non-blank cell beyond column A is not the header.

    BNMRK 'Table 6 - ACPT' has exactly this shape: a decorative 'Performance Year'
    banner sitting above the real header row.
    """
    sheet = [
        ["Table 6", None, None, None],
        ["Accountable Care Prospective Trend", None, None, None],
        [None, None, "Performance Year", None],       # decorative — 1 cell only
        ["ACPT Calculation", "BY3", "PY1", "PY2"],    # real header — 3 cells
        ["[F] ACPT", None, None, None],
        ["      ESRD", None, "1.049", "1.090"],
    ]
    path = make_xlsx(tmp_path, {"Table 6 - ACPT": sheet})
    conn = run_processor(test_session, defs_for(pattern="Table 6 - %"), [path])

    labels = sorted({r[0] for r in conn.execute(
        "SELECT DISTINCT column_label FROM raw_data.sheet_table"
    ).fetchall()})
    assert labels == ["BY3", "PY1", "PY2"]
    assert "Performance Year" not in labels

    assert conn.execute(
        "SELECT value_text FROM raw_data.sheet_table "
        "WHERE section_code = 'F' AND column_label = 'PY1' AND row_label = 'ESRD'"
    ).fetchone()[0] == "1.049"


def test_header_row_hint_overrides_detection(test_session, tmp_path):
    """header_row_hint wins over auto-detection."""
    sheet = [
        ["Table 1", None, None],
        ["Decoy Header", "WRONG1", "WRONG2"],   # auto-detection would choose this
        ["Real Header", "RIGHT1", "RIGHT2"],
        ["ESRD", "1", "2"],
    ]
    path = make_xlsx(tmp_path, {"Table 1 - Benchmark": sheet})

    conn = run_processor(test_session, defs_for(), [path])
    assert conn.execute(
        "SELECT DISTINCT column_label FROM raw_data.sheet_table ORDER BY 1"
    ).fetchall() == [("WRONG1",), ("WRONG2",)]

    conn = run_processor(test_session, defs_for(header_row_hint=3), [path])
    assert conn.execute(
        "SELECT DISTINCT column_label FROM raw_data.sheet_table ORDER BY 1"
    ).fetchall() == [("RIGHT1",), ("RIGHT2",)]


def test_column_with_blank_header_is_excluded(test_session, tmp_path):
    """A value column whose header cell is blank is dropped entirely."""
    sheet = [
        ["Table 1", None, None, None],
        ["Calculation", "BY1", None, "BY3"],
        ["ESRD", "1", "2", "3"],
    ]
    path = make_xlsx(tmp_path, {"Table 1 - Benchmark": sheet})
    conn = run_processor(test_session, defs_for(), [path])

    assert conn.execute(
        "SELECT DISTINCT column_label FROM raw_data.sheet_table ORDER BY 1"
    ).fetchall() == [("BY1",), ("BY3",)]
    assert "2" not in {
        r[0] for r in conn.execute(
            "SELECT value_text FROM raw_data.sheet_table"
        ).fetchall()
    }


def test_missing_header_row_raises(test_session, tmp_path):
    """A sheet with no row carrying 2+ value cells is an error, not a silent skip."""
    sheet = [
        ["Table 1", None, None],
        ["Only one cell", "X", None],
        ["Another", None, None],
    ]
    path = make_xlsx(tmp_path, {"Table 1 - Benchmark": sheet})
    with pytest.raises(RuntimeError, match="No header row found"):
        run_processor(test_session, defs_for(), [path])


# ---------------------------------------------------------------------------
# Section hierarchy
# ---------------------------------------------------------------------------


def test_unbracketed_section_sets_group_label(test_session, tmp_path):
    """An unbracketed section row sets both GROUP_LABEL and SECTION_LABEL."""
    path = make_xlsx(tmp_path, {"Table 1 - Benchmark": BASIC_SHEET})
    conn = run_processor(test_session, defs_for(), [path])

    row = conn.execute(
        "SELECT group_label, section_code, section_label FROM raw_data.sheet_table "
        "WHERE row_num = 7 AND column_label = 'BY1'"
    ).fetchone()
    assert row[0] == "Assigned Beneficiaries"

    # A data row directly under an unbracketed section carries no section code.
    sheet = [
        ["Table 1", None, None],
        ["Calculation", "BY1", "BY2"],
        ["Regional Expenditures ($)", None, None],
        ["ESRD", "1", "2"],
    ]
    path = make_xlsx(tmp_path, {"Table 1 - Benchmark": sheet}, name="unbracketed.xlsx")
    conn = run_processor(test_session, defs_for(), [path])
    assert conn.execute(
        "SELECT DISTINCT group_label, section_code, section_label "
        "FROM raw_data.sheet_table"
    ).fetchall() == [("Regional Expenditures ($)", None, "Regional Expenditures ($)")]


def test_bracketed_section_sets_code_and_preserves_group(test_session, tmp_path):
    """A bracketed section row sets SECTION_CODE/LABEL and leaves GROUP_LABEL alone."""
    path = make_xlsx(tmp_path, {"Table 1 - Benchmark": BASIC_SHEET})
    conn = run_processor(test_session, defs_for(), [path])

    assert conn.execute(
        "SELECT DISTINCT group_label, section_code, section_label "
        "FROM raw_data.sheet_table WHERE row_num = 12"
    ).fetchall() == [("Trended Expenditures", "B", "Per Capita Expenditures ($)")]


def test_section_with_values_emits_own_row_and_sets_section(test_session, tmp_path):
    """Rule 9: '[A2] Person Years | 5622 | 5778' is a DATA row that also opens A2.

    Its own totals must be emitted with the bracket prefix stripped, and every
    following data row must inherit SECTION_CODE = 'A2'.
    """
    path = make_xlsx(tmp_path, {"Table 1 - Benchmark": BASIC_SHEET})
    conn = run_processor(test_session, defs_for(), [path])

    # The [A2] row emits its own data.
    assert conn.execute(
        "SELECT section_code, section_label, row_label, column_label, value_text "
        "FROM raw_data.sheet_table WHERE row_num = 8 ORDER BY column_label"
    ).fetchall() == [
        ("A2", "Person Years", "Person Years", "BY1", "5622"),
        ("A2", "Person Years", "Person Years", "BY2", "5778"),
    ]

    # ...and the following row inherits A2 (not A1) plus the enclosing group.
    assert conn.execute(
        "SELECT DISTINCT group_label, section_code, section_label, row_label "
        "FROM raw_data.sheet_table WHERE row_num = 9"
    ).fetchall() == [("Assigned Beneficiaries", "A2", "Person Years", "ESRD")]

    # The preceding [A1] row opened its own section for itself only.
    assert conn.execute(
        "SELECT DISTINCT section_code FROM raw_data.sheet_table WHERE row_num = 7"
    ).fetchall() == [("A1",)]


def test_pure_section_rows_are_not_emitted(test_session, tmp_path):
    """A section row with no values contributes state but no output rows."""
    path = make_xlsx(tmp_path, {"Table 1 - Benchmark": BASIC_SHEET})
    conn = run_processor(test_session, defs_for(), [path])

    emitted = {r[0] for r in conn.execute(
        "SELECT DISTINCT row_num FROM raw_data.sheet_table"
    ).fetchall()}
    assert emitted == {7, 8, 9, 12}


# ---------------------------------------------------------------------------
# Unpivot
# ---------------------------------------------------------------------------


def test_null_value_rows_are_emitted(test_session, tmp_path):
    """Empty future-quarter cells still produce rows — the full grid is wanted."""
    sheet = [
        ["Table 2", None, None, None],
        [None, "Benchmark Year 3", "Q1", "Q2"],
        ["Regional Expenditures ($)", None, None, None],
        ["ESRD", "125734", "132431", None],
    ]
    path = make_xlsx(tmp_path, {"Table 2 - Regional": sheet})
    conn = run_processor(test_session, defs_for(pattern="Table 2 - %"), [path])

    assert conn.execute("SELECT COUNT(*) FROM raw_data.sheet_table").fetchone()[0] == 3
    assert conn.execute(
        "SELECT value_text FROM raw_data.sheet_table WHERE column_label = 'Q2'"
    ).fetchone()[0] is None


def test_dash_values_are_preserved_verbatim(test_session, tmp_path):
    """'-' is data, not a null — it survives as text."""
    path = make_xlsx(tmp_path, {"Table 1 - Benchmark": BASIC_SHEET})
    conn = run_processor(test_session, defs_for(), [path])

    assert conn.execute(
        "SELECT value_text FROM raw_data.sheet_table "
        "WHERE row_num = 9 AND column_label = 'BY1'"
    ).fetchone()[0] == "-"


# ---------------------------------------------------------------------------
# Label normalization
# ---------------------------------------------------------------------------


def test_label_normalization(test_session, tmp_path):
    """Trim, then strip a trailing digits-only '[n]' footnote marker, then trim."""
    sheet = [
        ["Table 2", None, None, None],
        [None, "Benchmark Year 3[4]", "Q2 ", "Q3[12]"],
        ["Regional Expenditures ($)[1]", None, None, None],
        ["      ESRD", "1", "2", "3"],
    ]
    path = make_xlsx(tmp_path, {"Table 2 - Regional": sheet})
    conn = run_processor(test_session, defs_for(pattern="Table 2 - %"), [path])

    assert sorted(r[0] for r in conn.execute(
        "SELECT DISTINCT column_label FROM raw_data.sheet_table"
    ).fetchall()) == ["Benchmark Year 3", "Q2", "Q3"]

    assert conn.execute(
        "SELECT DISTINCT group_label, section_label, row_label FROM raw_data.sheet_table"
    ).fetchall() == [
        ("Regional Expenditures ($)", "Regional Expenditures ($)", "ESRD")
    ]


def test_leading_bracket_is_a_section_code_not_a_footnote(test_session, tmp_path):
    """Only TRAILING digits-only markers are stripped; a leading [A1] is a code."""
    path = make_xlsx(tmp_path, {"Table 1 - Benchmark": BASIC_SHEET})
    conn = run_processor(test_session, defs_for(), [path])

    assert conn.execute(
        "SELECT section_code, row_label FROM raw_data.sheet_table "
        "WHERE row_num = 7 AND column_label = 'BY1'"
    ).fetchone() == ("A1", "Assigned Beneficiaries")


# ---------------------------------------------------------------------------
# Notes and footnotes
# ---------------------------------------------------------------------------


def test_notes_and_footnote_rows_are_dropped(test_session, tmp_path):
    """Everything at/after the first 'Note...' row disappears from the output."""
    path = make_xlsx(tmp_path, {"Table 1 - Benchmark": BASIC_SHEET})
    conn = run_processor(test_session, defs_for(), [path])

    labels = {r[0] for r in conn.execute(
        "SELECT row_label FROM raw_data.sheet_table "
        "UNION ALL SELECT section_label FROM raw_data.sheet_table "
        "UNION ALL SELECT group_label FROM raw_data.sheet_table"
    ).fetchall()}
    assert not any(str(label).upper().startswith("NOTE") for label in labels if label)
    assert not any(str(label).startswith("[1]") for label in labels if label)


def test_footnote_definition_row_with_values_is_dropped(test_session, tmp_path):
    """A '[n]...' row is a footnote definition even when it carries cell values."""
    sheet = [
        ["Table 1", None, None],
        ["Calculation", "BY1", "BY2"],
        ["ESRD", "1", "2"],
        ['[10]See "Parameters" tab for details.', "x", "y"],
    ]
    path = make_xlsx(tmp_path, {"Table 1 - Benchmark": sheet})
    conn = run_processor(test_session, defs_for(), [path])

    assert conn.execute(
        "SELECT DISTINCT row_label FROM raw_data.sheet_table"
    ).fetchall() == [("ESRD",)]


def test_include_notes_keeps_the_notes_block(test_session, tmp_path):
    """include_notes=True disables both the 'Note' cutoff and the [n] filter."""
    sheet = [
        ["Table 1", None, None],
        ["Calculation", "BY1", "BY2"],
        ["Alpha", "1", "2"],
        ["NOTES:", None, None],
        ["[1]A footnote definition.", None, None],
        ["Extra", "3", "4"],
    ]
    path = make_xlsx(tmp_path, {"Table 1 - Benchmark": sheet})

    conn = run_processor(test_session, defs_for(), [path])
    assert conn.execute(
        "SELECT DISTINCT row_label FROM raw_data.sheet_table"
    ).fetchall() == [("Alpha",)]

    conn = run_processor(test_session, defs_for(include_notes=True), [path])
    assert sorted(r[0] for r in conn.execute(
        "SELECT DISTINCT row_label FROM raw_data.sheet_table"
    ).fetchall()) == ["Alpha", "Extra"]


def test_sheet_without_any_notes(test_session, tmp_path):
    """A sheet that ends on a data row needs no notes handling."""
    sheet = [
        ["Table 2", None, None],
        [None, "Q1", "Q2"],
        ["Regional Weight", None, None],
        ["ESRD", "0.99", "0.98"],
        ["Disabled", "0.95", "0.94"],
    ]
    path = make_xlsx(tmp_path, {"Table 2 - Regional": sheet})
    conn = run_processor(test_session, defs_for(pattern="Table 2 - %"), [path])

    assert conn.execute("SELECT COUNT(*) FROM raw_data.sheet_table").fetchone()[0] == 4
    assert conn.execute(
        "SELECT DISTINCT group_label FROM raw_data.sheet_table"
    ).fetchall() == [("Regional Weight",)]


# ---------------------------------------------------------------------------
# Sheet discovery across files
# ---------------------------------------------------------------------------


def test_file_without_matching_sheet_is_skipped(test_session, tmp_path):
    """BNMRK 'Table 6' is absent from some deliveries — skip that file silently."""
    with_t6 = make_xlsx(
        tmp_path,
        {"Table 1 - Benchmark": BASIC_SHEET,
         "Table 6 - ACPT": [
             ["Table 6", None, None],
             ["ACPT Calculation", "PY1", "PY2"],
             ["[F] ACPT", None, None],
             ["ESRD", "1.049", "1.090"],
         ]},
        name="with_t6.xlsx",
    )
    without_t6 = make_xlsx(
        tmp_path, {"Table 1 - Benchmark": BASIC_SHEET}, name="without_t6.xlsx"
    )

    conn = run_processor(
        test_session,
        [SheetDef(table_name="SHEET_TABLE", sheet_pattern="Table 1 - %"),
         SheetDef(table_name="ACPT_TABLE", sheet_pattern="Table 6 - %")],
        [with_t6, without_t6],
    )

    # Table 1 loads from both files...
    assert sorted(r[0] for r in conn.execute(
        "SELECT DISTINCT file_name FROM raw_data.sheet_table"
    ).fetchall()) == ["with_t6.xlsx", "without_t6.xlsx"]

    # ...while Table 6 loads only from the file that has it, without erroring.
    assert conn.execute(
        "SELECT DISTINCT file_name FROM raw_data.acpt_table"
    ).fetchall() == [("with_t6.xlsx",)]


def test_batch_with_no_matching_sheet_yields_an_empty_table(test_session, tmp_path):
    """A batch where no file has the sheet contributes 0 rows — it is not an error.

    run() calls _build_query() once per batch, so "no matching sheet" has to be a
    per-batch non-event: a whole batch can legitimately consist of Table-6-less
    deliveries.
    """
    path = make_xlsx(tmp_path, {"Table 1 - Benchmark": BASIC_SHEET})
    conn = run_processor(test_session, defs_for(pattern="Table 9 - %"), [path])

    assert conn.execute("SELECT COUNT(*) FROM raw_data.sheet_table").fetchone()[0] == 0
    assert [r[0] for r in conn.execute("DESCRIBE raw_data.sheet_table").fetchall()] == [
        "row_num", "group_label", "section_code", "section_label",
        "row_label", "column_group_label", "column_label", "value_text",
        "file_path", "directory_name", "file_name", "file_date",
    ]


def test_sheetless_file_in_a_later_batch_does_not_fail_the_table(test_session, tmp_path):
    """Regression: at batch size 1 the second, sheet-less file must not fail the run.

    Before the fix the "skip silently" of rule 1 was evaluated per batch, so the
    Table-6-less delivery raised and base.run() marked the whole table failed.
    """
    t6_sheet = [
        ["Table 6", None, None],
        ["ACPT Calculation", "PY1", "PY2"],
        ["[F] ACPT", None, None],
        ["ESRD", "1.049", "1.090"],
    ]
    with_t6 = make_xlsx(
        tmp_path,
        {"Table 6 - ACPT": t6_sheet, "Table 1 - Benchmark": BASIC_SHEET},
        name="with_t6.xlsx",
    )
    without_t6 = make_xlsx(
        tmp_path, {"Table 1 - Benchmark": BASIC_SHEET}, name="without_t6.xlsx"
    )
    one_per_batch = SimpleNamespace(
        ACO_ID="T0000", FULL_REFRESH=True, PROCESS_BATCH_SIZE_DEFAULT=1
    )

    conn = run_processor(
        test_session,
        defs_for(pattern="Table 6 - %", table="ACPT_TABLE"),
        [with_t6, without_t6],
        config=one_per_batch,
    )

    assert conn.execute(
        "SELECT DISTINCT file_name FROM raw_data.acpt_table"
    ).fetchall() == [("with_t6.xlsx",)]
    assert conn.execute(
        "SELECT value_text FROM raw_data.acpt_table "
        "WHERE row_label = 'ESRD' AND column_label = 'PY1'"
    ).fetchone()[0] == "1.049"


def test_ambiguous_sheet_pattern_raises(test_session, tmp_path):
    """'Table 1%' also matches 'Table 1B - ...' — reading either silently is worse."""
    path = make_xlsx(
        tmp_path,
        {"Table 1 - Historical Benchmark": BASIC_SHEET,
         "Table 1B - Prior Savings Adj": BASIC_SHEET},
    )
    with pytest.raises(RuntimeError, match="ambiguous") as excinfo:
        run_processor(test_session, defs_for(pattern="Table 1%"), [path])

    message = str(excinfo.value)
    assert "Table 1 - Historical Benchmark" in message
    assert "Table 1B - Prior Savings Adj" in message

    # The narrowed pattern selects exactly one sheet and works.
    conn = run_processor(test_session, defs_for(pattern="Table 1 - %"), [path])
    assert conn.execute("SELECT COUNT(*) FROM raw_data.sheet_table").fetchone()[0] > 0


# ---------------------------------------------------------------------------
# File metadata hook
# ---------------------------------------------------------------------------


def test_default_file_metadata_columns(test_session, tmp_path):
    """The base hook emits FILE_PATH / DIRECTORY_NAME / FILE_NAME and a NULL date."""
    path = make_xlsx(tmp_path, {"Table 1 - Benchmark": BASIC_SHEET})
    conn = run_processor(test_session, defs_for(), [path])

    row = conn.execute(
        "SELECT DISTINCT file_path, directory_name, file_name, file_date "
        "FROM raw_data.sheet_table"
    ).fetchone()
    assert row[0] == path.replace("\\", "/")
    assert row[1] == str(tmp_path).replace("\\", "/")
    assert row[2] == "report.xlsx"
    assert row[3] is None


def test_file_metadata_hook_is_overridable(test_session, tmp_path):
    """Subclasses can append report-specific metadata columns."""

    class _WithPeriod(_Processor):
        def _file_metadata_sql(self, xlsx_path):
            metadata = super()._file_metadata_sql(xlsx_path)
            metadata["PERIOD"] = "'2026Q1'"
            metadata["FILE_DATE"] = "DATE '2026-03-31'"
            return metadata

    path = make_xlsx(tmp_path, {"Table 1 - Benchmark": BASIC_SHEET})
    conn = run_processor(
        test_session, defs_for(), [path], processor_cls=_WithPeriod
    )

    cols = [r[0] for r in conn.execute("DESCRIBE raw_data.sheet_table").fetchall()]
    assert cols[-1] == "period"
    assert conn.execute(
        "SELECT DISTINCT period, file_date FROM raw_data.sheet_table"
    ).fetchall() == [("2026Q1", date(2026, 3, 31))]


def test_sheet_with_only_a_label_column_raises(test_session, tmp_path):
    """A matched sheet whose used range is column A only has no header row."""
    path = make_xlsx(tmp_path, {"Table 1 - Benchmark": [["Table 1"], ["Subtitle"]]})
    with pytest.raises(RuntimeError, match="No header row found"):
        run_processor(test_session, defs_for(), [path])


def test_header_row_hint_past_end_of_sheet_raises(test_session, tmp_path):
    """A hint outside the sheet is a configuration error, not a silent skip."""
    path = make_xlsx(
        tmp_path, {"Table 1 - Benchmark": [["Table 1", "BY1", "BY2"], ["ESRD", "1", "2"]]}
    )
    with pytest.raises(RuntimeError, match="No header row found") as excinfo:
        run_processor(test_session, defs_for(header_row_hint=99), [path])
    assert "header_row_hint=99" in str(excinfo.value)


def test_header_row_hint_at_a_blank_row_raises(test_session, tmp_path):
    """A hint pointing at a row with no labels reports the rule-2 error too."""
    sheet = [
        ["Table 1", None, None],
        [None, None, None],
        ["Calculation", "BY1", "BY2"],
        ["ESRD", "1", "2"],
    ]
    path = make_xlsx(tmp_path, {"Table 1 - Benchmark": sheet})
    with pytest.raises(RuntimeError, match="No header row found"):
        run_processor(test_session, defs_for(header_row_hint=2), [path])


def test_code_only_bracketed_label_falls_back_to_the_code(test_session, tmp_path):
    """'[A2]' with no remainder still gets a label rather than a NULL one."""
    sheet = [
        ["Table 1", None, None],
        ["Calculation", "BY1", "BY2"],
        ["[A2]", "5", "6"],
        ["ESRD", "7", "8"],
    ]
    path = make_xlsx(tmp_path, {"Table 1 - Benchmark": sheet})
    conn = run_processor(test_session, defs_for(), [path])

    assert conn.execute(
        "SELECT DISTINCT section_code, section_label, row_label "
        "FROM raw_data.sheet_table WHERE row_num = 3"
    ).fetchall() == [("A2", "A2", "A2")]
    assert conn.execute(
        "SELECT DISTINCT section_code, section_label, row_label "
        "FROM raw_data.sheet_table WHERE row_num = 4"
    ).fetchall() == [("A2", "A2", "ESRD")]


# ---------------------------------------------------------------------------
# Forward-fill semantics
# ---------------------------------------------------------------------------


def test_unbracketed_section_clears_a_preceding_section_code(test_session, tmp_path):
    """An unbracketed section must RESET SECTION_CODE to NULL, not inherit it.

    This is the case an IGNORE NULLS forward-fill gets wrong: without a
    preceding bracketed section the bug is invisible.
    """
    sheet = [
        ["Table 1", None, None],
        ["Calculation", "BY1", "BY2"],
        ["[A] Coded Section", None, None],
        ["ESRD", "1", "2"],
        ["Plain Group", None, None],
        ["Disabled", "3", "4"],
    ]
    path = make_xlsx(tmp_path, {"Table 1 - Benchmark": sheet})
    conn = run_processor(test_session, defs_for(), [path])

    assert conn.execute(
        "SELECT DISTINCT group_label, section_code, section_label "
        "FROM raw_data.sheet_table WHERE row_label = 'ESRD'"
    ).fetchall() == [(None, "A", "Coded Section")]
    assert conn.execute(
        "SELECT DISTINCT group_label, section_code, section_label "
        "FROM raw_data.sheet_table WHERE row_label = 'Disabled'"
    ).fetchall() == [("Plain Group", None, "Plain Group")]


# ---------------------------------------------------------------------------
# Emission order and unions
# ---------------------------------------------------------------------------


def test_columns_are_emitted_in_sheet_order(test_session, tmp_path):
    """Rule 10: one row per retained column, in the sheet's own column order."""
    sheet = [
        ["Table 1", None, None, None],
        ["Calculation", "Zed", "Alpha", "Mid"],
        ["ESRD", "1", "2", "3"],
    ]
    path = make_xlsx(tmp_path, {"Table 1 - Benchmark": sheet})
    conn = run_processor(test_session, defs_for(), [path])

    assert conn.execute(
        "SELECT column_label, value_text FROM raw_data.sheet_table"
    ).fetchall() == [("Zed", "1"), ("Alpha", "2"), ("Mid", "3")]


def test_files_with_different_headers_union_into_one_table(test_session, tmp_path):
    """Two deliveries whose column labels differ land in the same long table."""
    first = make_xlsx(
        tmp_path,
        {"Table 1 - Benchmark": [
            ["Table 1", None, None],
            ["Calculation", "BY1", "BY2"],
            ["ESRD", "1", "2"],
        ]},
        name="first.xlsx",
    )
    second = make_xlsx(
        tmp_path,
        {"Table 1 - Benchmark": [
            ["Table 1", None, None],
            ["Calculation", "BY2", "BY3"],
            ["ESRD", "3", "4"],
        ]},
        name="second.xlsx",
    )
    conn = run_processor(test_session, defs_for(), [first, second])

    assert sorted(conn.execute(
        "SELECT file_name, column_label, value_text FROM raw_data.sheet_table"
    ).fetchall()) == [
        ("first.xlsx", "BY1", "1"),
        ("first.xlsx", "BY2", "2"),
        ("second.xlsx", "BY2", "3"),
        ("second.xlsx", "BY3", "4"),
    ]


def test_quote_characters_in_labels_survive(test_session, tmp_path):
    """Apostrophes and double quotes in labels are data, not SQL."""
    sheet = [
        ["Table 1", None, None],
        ["Calculation", "ACO's Share", 'The "National" Rate'],
        ["Beneficiaries' Group", None, None],
        ["[A1] O'Brien's Row", "1", "2"],
    ]
    path = make_xlsx(tmp_path, {"Table 1 - Benchmark": sheet})
    conn = run_processor(test_session, defs_for(), [path])

    assert conn.execute(
        "SELECT group_label, section_code, section_label, row_label, "
        "column_label, value_text FROM raw_data.sheet_table ORDER BY column_label"
    ).fetchall() == [
        ("Beneficiaries' Group", "A1", "O'Brien's Row", "O'Brien's Row",
         "ACO's Share", "1"),
        ("Beneficiaries' Group", "A1", "O'Brien's Row", "O'Brien's Row",
         'The "National" Rate', "2"),
    ]


# ---------------------------------------------------------------------------
# Two-row headers
# ---------------------------------------------------------------------------

# The real SNF report shape: a merged primary header spanning C:E is reported
# only in column C, and columns D/E carry only their sub-labels.
SNF_SHEET = [
    ["Table 3", None, None, None, None, None, None, None],
    ["Skilled Nursing Facility (SNF) Report", None, None, None, None, None, None, None],
    [None, "ACO-Specific[1]", "ACO-Specific Stays at Affiliated SNFs", None, None,
     "ACO-Specific Stays at Non-Affiliated SNFs", "All MSSP ACOs[4]",
     "National Assignable FFS 12-Month[5]"],
    [None, None, "Total", "With Prior 3-Day  Hospital Stay[3]",
     " Without Prior 3-Day Hospital Stay[3]", None, None, None],
    ["Number of SNF Stays", None, None, None, None, None, None, None],
    ["Admissions[6]", "439", "-", "-", "-", "-", "516", "1260787"],
]


def test_two_row_header_merges_into_group_and_label(test_session, tmp_path):
    """All 7 value columns survive; the merged primary fills only C:E."""
    path = make_xlsx(tmp_path, {"Table 3 - SNF": SNF_SHEET})
    conn = run_processor(test_session, defs_for(pattern="Table 3 - %"), [path])

    assert conn.execute(
        "SELECT column_group_label, column_label, value_text FROM raw_data.sheet_table "
        "WHERE row_label = 'Admissions'"
    ).fetchall() == [
        (None, "ACO-Specific", "439"),
        ("ACO-Specific Stays at Affiliated SNFs", "Total", "-"),
        # The fixture keeps the real workbook's double space; rule 5 collapses it.
        ("ACO-Specific Stays at Affiliated SNFs", "With Prior 3-Day Hospital Stay", "-"),
        ("ACO-Specific Stays at Affiliated SNFs", "Without Prior 3-Day Hospital Stay", "-"),
        (None, "ACO-Specific Stays at Non-Affiliated SNFs", "-"),
        (None, "All MSSP ACOs", "516"),
        (None, "National Assignable FFS 12-Month", "1260787"),
    ]

    # The continuation row is part of the header, so it is not a data row, and
    # the section row below it still works.
    assert conn.execute(
        "SELECT DISTINCT group_label FROM raw_data.sheet_table"
    ).fetchall() == [("Number of SNF Stays",)]


def test_data_row_with_blank_column_a_is_not_a_continuation_row(test_session, tmp_path):
    """The guard: a blank column A alone must not turn a data row into labels.

    Here every column has a primary header, so no column pairs a blank primary
    with a non-blank sub-label and the row stays data.
    """
    sheet = [
        ["Table 1", None, None],
        ["Calculation", "BY1", "BY2"],
        [None, "1", "2"],
        ["ESRD", "3", "4"],
    ]
    path = make_xlsx(tmp_path, {"Table 1 - Benchmark": sheet})
    conn = run_processor(test_session, defs_for(), [path])

    assert conn.execute(
        "SELECT DISTINCT column_group_label, column_label FROM raw_data.sheet_table "
        "ORDER BY column_label"
    ).fetchall() == [(None, "BY1"), (None, "BY2")]
    # The blank-column-A row is skipped as a row (rule 6), not read as a header.
    assert conn.execute(
        "SELECT DISTINCT row_label FROM raw_data.sheet_table"
    ).fetchall() == [("ESRD",)]
    assert "1" not in {
        r[0] for r in conn.execute(
            "SELECT column_label FROM raw_data.sheet_table"
        ).fetchall()
    }


def test_column_blank_in_both_header_rows_is_excluded(test_session, tmp_path):
    """Rule 4 is unchanged by two-row headers."""
    sheet = [
        ["Table 3", None, None, None, None],
        [None, "Primary A", "Merged", None, None],
        [None, None, "Sub One", "Sub Two", None],
        ["ESRD", "1", "2", "3", "4"],
    ]
    path = make_xlsx(tmp_path, {"Table 3 - SNF": sheet})
    conn = run_processor(test_session, defs_for(pattern="Table 3 - %"), [path])

    assert conn.execute(
        "SELECT column_group_label, column_label, value_text FROM raw_data.sheet_table"
    ).fetchall() == [
        (None, "Primary A", "1"),
        ("Merged", "Sub One", "2"),
        ("Merged", "Sub Two", "3"),
    ]


def test_header_row_hint_selects_the_primary_row_of_a_two_row_header(
    test_session, tmp_path
):
    """A hint names the primary row; the continuation row is still picked up."""
    path = make_xlsx(tmp_path, {"Table 3 - SNF": SNF_SHEET})
    conn = run_processor(
        test_session, defs_for(pattern="Table 3 - %", header_row_hint=3), [path]
    )

    assert conn.execute(
        "SELECT COUNT(*) FROM raw_data.sheet_table WHERE row_label = 'Admissions'"
    ).fetchone()[0] == 7
    assert conn.execute(
        "SELECT DISTINCT column_group_label FROM raw_data.sheet_table "
        "WHERE column_label = 'Total'"
    ).fetchall() == [("ACO-Specific Stays at Affiliated SNFs",)]


def test_single_header_sheets_have_null_column_group_label(test_session, tmp_path):
    """Regression guard: nothing about single-header sheets changed."""
    path = make_xlsx(tmp_path, {"Table 1 - Benchmark": BASIC_SHEET})
    conn = run_processor(test_session, defs_for(), [path])

    assert conn.execute(
        "SELECT DISTINCT column_group_label FROM raw_data.sheet_table"
    ).fetchall() == [(None,)]
    assert conn.execute(
        "SELECT COUNT(*) FROM raw_data.sheet_table WHERE column_group_label IS NOT NULL"
    ).fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Interior whitespace
# ---------------------------------------------------------------------------


def test_interior_whitespace_in_labels_is_collapsed(test_session, tmp_path):
    """Rule 5 collapses every run of whitespace to one space, in every label.

    Excel exports carry tabs, embedded newlines and non-breaking spaces as
    readily as doubled spaces, and a label that merely *looks* right breaks any
    dbt model that joins or filters on it.
    """
    sheet = [
        ["Table 3", None, None, None, None],
        # \xa0 is the non-breaking space Excel emits; RE2's \s does not match it.
        [None, "ACO\xa0Specific", "Merged  Primary", None, None],
        [None, None, "With Prior 3-Day  Hospital Stay", "Performance Year\nPY1", None],
        ["Number of  SNF\tStays", None, None, None, None],
        ["[A1]  Admissions\xa0Total", "1", "2", "3", "4"],
    ]
    path = make_xlsx(tmp_path, {"Table 3 - SNF": sheet})
    conn = run_processor(test_session, defs_for(pattern="Table 3 - %"), [path])

    assert conn.execute(
        "SELECT group_label, section_code, section_label, row_label, "
        "column_group_label, column_label FROM raw_data.sheet_table "
        "ORDER BY row_num, column_label"
    ).fetchall() == [
        ("Number of SNF Stays", "A1", "Admissions Total", "Admissions Total",
         None, "ACO Specific"),
        ("Number of SNF Stays", "A1", "Admissions Total", "Admissions Total",
         "Merged Primary", "Performance Year PY1"),
        ("Number of SNF Stays", "A1", "Admissions Total", "Admissions Total",
         "Merged Primary", "With Prior 3-Day Hospital Stay"),
    ]


def test_whitespace_collapse_precedes_footnote_stripping(test_session, tmp_path):
    """A footnote marker separated by odd whitespace is still stripped."""
    sheet = [
        ["Table 1", None, None],
        ["Calculation", "Regional Expenditures ($)\xa0[1]", "Benchmark Year 3\t[4]"],
        ["ESRD", "1", "2"],
    ]
    path = make_xlsx(tmp_path, {"Table 1 - Benchmark": sheet})
    conn = run_processor(test_session, defs_for(), [path])

    assert conn.execute(
        "SELECT column_label FROM raw_data.sheet_table"
    ).fetchall() == [("Regional Expenditures ($)",), ("Benchmark Year 3",)]


def test_value_text_is_not_whitespace_normalized(test_session, tmp_path):
    """VALUE_TEXT is verbatim — only labels are normalized."""
    sheet = [
        ["Table 1", None, None],
        ["Calculation", "BY1", "BY2"],
        ["ESRD", "  12  34  ", "a\tb"],
    ]
    path = make_xlsx(tmp_path, {"Table 1 - Benchmark": sheet})
    conn = run_processor(test_session, defs_for(), [path])

    assert conn.execute(
        "SELECT value_text FROM raw_data.sheet_table ORDER BY column_label"
    ).fetchall() == [("  12  34  ",), ("a\tb",)]


# ---------------------------------------------------------------------------
# Key/value sheets (synthetic_column_labels)
# ---------------------------------------------------------------------------

# The real "Parameters" tab: two columns, no header row anywhere, and rows whose
# column B is blank acting as section headers for the rows beneath them.
PARAMETERS_SHEET = [
    ["Parameters", None],                                        # 1  section
    ["Table of Contents", None],                                 # 2  section
    ["ACO Track", "BASIC Level A"],                              # 3  data
    ["ACO Agreement Period", "1"],                               # 4  data
    ["Participant List Performance Year", "2025"],               # 5  data
    ["Assignment Methodology", "Preliminary Prospective"],        # 6  data
    ["Claims-Based Beneficiary Assignment Window", None],        # 7  section
    ["Benchmark Year 1 (BY1)", "01/01/2022 - 12/31/2022"],       # 8  data
    ["Benchmark Year 2 (BY2)", "01/01/2023 - 12/31/2023"],       # 9  data
]


def test_synthetic_labels_read_a_key_value_sheet(test_session, tmp_path):
    """Header detection would fail on a 2-column sheet; the labels stand in for it.

    Data starts at row 1, so the first row is not swallowed as a header, and
    COLUMN_LABEL comes from the def rather than from the sheet.
    """
    path = make_xlsx(tmp_path, {"Parameters": PARAMETERS_SHEET})
    conn = run_processor(
        test_session,
        defs_for(pattern="Parameters", synthetic_column_labels=("VALUE",)),
        [path],
    )

    assert conn.execute(
        "SELECT DISTINCT column_label, column_group_label FROM raw_data.sheet_table"
    ).fetchall() == [("VALUE", None)]

    assert conn.execute(
        "SELECT value_text FROM raw_data.sheet_table WHERE row_label = 'ACO Track'"
    ).fetchone()[0] == "BASIC Level A"

    # Row 3 is the first emitted row: nothing above it was consumed as a header.
    assert conn.execute(
        "SELECT MIN(row_num) FROM raw_data.sheet_table"
    ).fetchone()[0] == 3


def test_synthetic_labels_without_them_the_same_sheet_raises(test_session, tmp_path):
    """The mode exists because header detection genuinely cannot cope here."""
    path = make_xlsx(tmp_path, {"Parameters": PARAMETERS_SHEET})
    with pytest.raises(RuntimeError, match="No header row found"):
        run_processor(test_session, defs_for(pattern="Parameters"), [path])


def test_synthetic_labels_treat_a_blank_second_column_as_a_section(test_session, tmp_path):
    """A row with a blank column B is a section header, exactly as elsewhere."""
    path = make_xlsx(tmp_path, {"Parameters": PARAMETERS_SHEET})
    conn = run_processor(
        test_session,
        defs_for(pattern="Parameters", synthetic_column_labels=("VALUE",)),
        [path],
    )

    # The BY rows inherit the window they sit under...
    assert conn.execute(
        "SELECT DISTINCT group_label, section_code, section_label "
        "FROM raw_data.sheet_table WHERE row_label LIKE 'Benchmark Year%'"
    ).fetchall() == [
        ("Claims-Based Beneficiary Assignment Window", None,
         "Claims-Based Beneficiary Assignment Window"),
    ]
    # ...and a section row contributes no data row of its own.
    assert conn.execute(
        "SELECT COUNT(*) FROM raw_data.sheet_table "
        "WHERE row_label = 'Claims-Based Beneficiary Assignment Window'"
    ).fetchone()[0] == 0


def test_synthetic_labels_exclude_columns_beyond_the_supplied_labels(
    test_session, tmp_path
):
    """One label, three value columns — C and D are dropped like blank-header ones."""
    sheet = [
        ["Alpha", "1", "ignored", "also ignored"],
        ["Beta", "2", "x", "y"],
    ]
    path = make_xlsx(tmp_path, {"Parameters": sheet})
    conn = run_processor(
        test_session,
        defs_for(pattern="Parameters", synthetic_column_labels=("VALUE",)),
        [path],
    )

    assert conn.execute(
        "SELECT row_label, column_label, value_text FROM raw_data.sheet_table "
        "ORDER BY row_num"
    ).fetchall() == [("Alpha", "VALUE", "1"), ("Beta", "VALUE", "2")]


def test_synthetic_labels_can_name_several_columns(test_session, tmp_path):
    """More than one label is supported; they are matched positionally from B."""
    sheet = [
        ["Alpha", "1", "2"],
        ["Beta", "3", "4"],
    ]
    path = make_xlsx(tmp_path, {"Parameters": sheet})
    conn = run_processor(
        test_session,
        defs_for(pattern="Parameters", synthetic_column_labels=("LOW", "HIGH")),
        [path],
    )

    assert conn.execute(
        "SELECT row_label, column_label, value_text FROM raw_data.sheet_table "
        "ORDER BY row_num, column_label"
    ).fetchall() == [
        ("Alpha", "HIGH", "2"), ("Alpha", "LOW", "1"),
        ("Beta", "HIGH", "4"), ("Beta", "LOW", "3"),
    ]


def test_synthetic_labels_on_a_label_only_sheet_raises(test_session, tmp_path):
    """No column B at all means the labels name nothing — a configuration error."""
    path = make_xlsx(tmp_path, {"Parameters": [["Alpha"], ["Beta"]]})
    with pytest.raises(RuntimeError, match="No header row found") as excinfo:
        run_processor(
            test_session,
            defs_for(pattern="Parameters", synthetic_column_labels=("VALUE",)),
            [path],
        )
    assert "synthetic_column_labels" in str(excinfo.value)


def test_synthetic_column_labels_default_leaves_behaviour_identical(
    test_session, tmp_path
):
    """The default (None) is the Phase 1 path, byte for byte.

    Guards against the new branch leaking into ordinary sheets: same rows, same
    labels, same values as a def that predates synthetic_column_labels.
    """
    path = make_xlsx(tmp_path, {"Table 1 - Benchmark": BASIC_SHEET})

    baseline = run_processor(test_session, defs_for(), [path]).execute(
        "SELECT row_num, group_label, section_code, section_label, row_label, "
        "column_group_label, column_label, value_text FROM raw_data.sheet_table "
        "ORDER BY row_num, column_label"
    ).fetchall()
    explicit_none = run_processor(
        test_session, defs_for(synthetic_column_labels=None), [path]
    ).execute(
        "SELECT row_num, group_label, section_code, section_label, row_label, "
        "column_group_label, column_label, value_text FROM raw_data.sheet_table "
        "ORDER BY row_num, column_label"
    ).fetchall()

    assert explicit_none == baseline
    assert baseline  # the comparison is only meaningful on a non-empty result


# ---------------------------------------------------------------------------
# LIKE escaping
# ---------------------------------------------------------------------------


def test_underscore_in_a_sheet_pattern_can_be_escaped(test_session, tmp_path):
    r"""'_' is LIKE's single-character wildcard; '\_' pins it to a real underscore."""
    sheet = [
        ["Table 1", None, None],
        ["Calculation", "BY1", "BY2"],
        ["ESRD", "1", "2"],
    ]
    path = make_xlsx(
        tmp_path, {"Table_1-Aggregate_EU_Report": sheet, "TableX1-Decoy": sheet}
    )

    # Unescaped, the '_' matches the 'X' too, and the ambiguity guard fires.
    with pytest.raises(RuntimeError, match="ambiguous"):
        run_processor(test_session, defs_for(pattern="Table_1-%"), [path])

    conn = run_processor(test_session, defs_for(pattern=r"Table\_1-%"), [path])
    assert conn.execute("SELECT COUNT(*) FROM raw_data.sheet_table").fetchone()[0] == 2


def test_synthetic_labels_do_not_truncate_at_a_note_row(test_session, tmp_path):
    """On a key/value sheet a key starting 'Note' is a parameter, not a cutoff.

    The trailing-notes rule is a sectioned-matrix concept: the block sits below
    the grid and everything from it down is commentary. A key/value sheet has no
    grid, and the real AEXPU Parameters tab carries a 'Note: ...' key two thirds
    of the way down — truncating there would silently drop every pair beneath it.
    """
    sheet = [
        ["ACO Track", "BASIC Level A"],
        ["Note: The RBCS code \"RX000N\" is excluded.", "N/A"],
        ["Claims Completion Factor", "1.072"],
        ["Date Produced", "04/28/2026"],
    ]
    path = make_xlsx(tmp_path, {"Parameters": sheet})
    conn = run_processor(
        test_session,
        defs_for(pattern="Parameters", synthetic_column_labels=("VALUE",)),
        [path],
    )

    assert [r[0] for r in conn.execute(
        "SELECT row_label FROM raw_data.sheet_table ORDER BY row_num"
    ).fetchall()] == [
        "ACO Track",
        'Note: The RBCS code "RX000N" is excluded.',
        "Claims Completion Factor",
        "Date Produced",
    ]


def test_synthetic_labels_still_drop_footnote_definition_rows(test_session, tmp_path):
    """The '^[n]' filter is unambiguous and stays on for key/value sheets."""
    sheet = [
        ["ACO Track", "BASIC Level A"],
        ['[10]See "Parameters" tab for details.', "x"],
        ["Date Produced", "04/28/2026"],
    ]
    path = make_xlsx(tmp_path, {"Parameters": sheet})
    conn = run_processor(
        test_session,
        defs_for(pattern="Parameters", synthetic_column_labels=("VALUE",)),
        [path],
    )

    assert [r[0] for r in conn.execute(
        "SELECT row_label FROM raw_data.sheet_table ORDER BY row_num"
    ).fetchall()] == ["ACO Track", "Date Produced"]


def test_notes_truncation_still_applies_to_matrix_sheets(test_session, tmp_path):
    """Regression guard: disabling the cutoff is scoped to key/value sheets only."""
    path = make_xlsx(tmp_path, {"Table 1 - Benchmark": BASIC_SHEET})
    conn = run_processor(test_session, defs_for(), [path])

    assert conn.execute(
        "SELECT COUNT(*) FROM raw_data.sheet_table WHERE row_label LIKE 'Note%'"
    ).fetchone()[0] == 0


def test_sheet_names_are_read_once_per_workbook(test_session, tmp_path):
    """Sheet discovery is memoized: N SheetDefs must not mean N workbook opens.

    A BNMRK workbook has ten defs, so an unmemoized _discover_sheet() opens and
    parses every delivery ten times — seconds locally, far worse over S3/ADLS.
    """
    sheets = {
        f"Table {n} - Sheet": [["Table", None, None], ["Calc", "BY1", "BY2"],
                               [f"Row {n}", "1", "2"]]
        for n in range(1, 6)
    }
    path = make_xlsx(tmp_path, sheets)

    class _Counting(_Processor):
        reads = 0

        def _sheet_names(self, xlsx_path):
            if xlsx_path not in self._sheet_names_cache:
                type(self).reads += 1
            return super()._sheet_names(xlsx_path)

    conn = run_processor(
        test_session,
        [SheetDef(table_name=f"T{n}", sheet_pattern=f"Table {n} - %")
         for n in range(1, 6)],
        [path],
        processor_cls=_Counting,
    )

    assert _Counting.reads == 1
    for n in range(1, 6):
        assert conn.execute(
            f"SELECT DISTINCT row_label FROM raw_data.t{n}"
        ).fetchall() == [(f"Row {n}",)]
