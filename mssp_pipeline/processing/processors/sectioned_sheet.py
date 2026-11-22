import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import duckdb

from .base import FileProcessor
from ..defs.sectioned_sheet_defs import SheetDef
from ..sql import sql_string_literal, validate_identifier


# Column A of a section row may carry a bracketed section code, e.g. "[A1] Person Years".
_SECTION_CODE_RE = r"^\s*\[([A-Za-z0-9]+)\]\s*(.*)$"

# A trailing footnote marker such as the "[4]" in "Benchmark Year 3[4]".
_FOOTNOTE_SUFFIX_RE = r"\[\d+\]\s*$"

# Any run of whitespace, collapsed to one space before anything else happens.
# RE2's \s is ASCII-only, so the non-breaking space Excel exports has to be
# spelled out; CMS headers also carry embedded newlines and tabs.
_WHITESPACE_RE = "[\\s\u00a0]+"

# Zero-width characters, removed outright before whitespace is collapsed. The
# settlement workbook's headers carry U+200B beside real whitespace
# ('Risk Score Weights\u00a0\u200b\n[A]'); turning it into a space would split
# a word wherever it sits inside one.
_ZERO_WIDTH_RE = "[\u200b\u200c\u200d\ufeff]"

# A footnote *definition* row, e.g. '[10]See "Parameters" tab for details.'
_FOOTNOTE_ROW_RE = r"^\s*\[\d+\]"

# The name Excel gives a blank header cell of a table (ListObject): 'Column1',
# 'Column2', ... It labels a spacer column, not data.
_PY_PLACEHOLDER_HEADER_RE = re.compile(r"Column\d+")

_PY_FOOTNOTE_SUFFIX_RE = re.compile(_FOOTNOTE_SUFFIX_RE)
_PY_WHITESPACE_RE = re.compile(_WHITESPACE_RE)
_PY_ZERO_WIDTH_RE = re.compile(_ZERO_WIDTH_RE)

# Grammar columns emitted ahead of the file metadata, with the types the real
# query produces. Used to synthesise an empty result for a batch that
# contributed no sheets.
_OUTPUT_COLUMNS: List[Tuple[str, str]] = [
    ("ROW_NUM", "BIGINT"),
    ("GROUP_LABEL", "VARCHAR"),
    ("SECTION_CODE", "VARCHAR"),
    ("SECTION_LABEL", "VARCHAR"),
    ("ROW_LABEL", "VARCHAR"),
    ("COLUMN_GROUP_LABEL", "VARCHAR"),
    ("COLUMN_LABEL", "VARCHAR"),
    ("VALUE_TEXT", "VARCHAR"),
]

# The same, for a processor with LAYOUT_COLUMNS: BLOCK_LABEL, ROW_KIND and
# COLUMN_CODE join the grammar columns.
_LAYOUT_OUTPUT_COLUMNS: List[Tuple[str, str]] = [
    ("ROW_NUM", "BIGINT"),
    ("BLOCK_LABEL", "VARCHAR"),
    ("GROUP_LABEL", "VARCHAR"),
    ("SECTION_CODE", "VARCHAR"),
    ("SECTION_LABEL", "VARCHAR"),
    ("ROW_LABEL", "VARCHAR"),
    ("ROW_KIND", "VARCHAR"),
    ("COLUMN_GROUP_LABEL", "VARCHAR"),
    ("COLUMN_CODE", "VARCHAR"),
    ("COLUMN_LABEL", "VARCHAR"),
    ("VALUE_TEXT", "VARCHAR"),
]

# A letter code closing a header label: 'Final Risk Ratio (Capped) [I]'.
_PY_COLUMN_CODE_RE = re.compile(r"^(.*\S)\s*\[([A-Z]{1,2})\]$")


@dataclass(frozen=True)
class _SheetLayout:
    """Per-sheet facts the layout-aware SELECT needs, read from the header."""

    # Column A's header label when column A is data (SheetDef.label_column_is_data).
    label_column_header: Optional[str] = None
    # SheetDef.blocks: the first block's title, and the cells of a header row,
    # [(column, normalized label)], so a repeat of it can be recognised.
    first_block_label: Optional[str] = None
    header_cells: Tuple[Tuple[str, Optional[str]], ...] = ()


class SectionedSheetProcessor(FileProcessor):
    """
    Generic reader for CMS "sectioned" xlsx data sheets.

    BNMRK (Historical Benchmark), AEXPU (annual Expenditure/Utilization) and
    QEXPU (quarterly Expenditure/Utilization) workbooks all share one grammar::

        row 1  | Table 1                                              <- title rows
        row 2  | Shared Savings Program Historical Benchmark Report
        row 5  | Historical Benchmark Calculation | BY1 | BY2 | BY3   <- HEADER row
        row 6  | Assigned Beneficiaries           |     |     |       <- section (unbracketed)
        row 7  | [A1] Assigned Beneficiaries      |5780 |5902 |5706   <- section WITH values
        row 9  | ESRD                             | -   | -   | 42    <- data row
        row63  | Note: Numerical values ...                           <- notes
        row64  | [A1] Number of Assigned Beneficiaries ...            <- footnote definition

    Some sheets (the SNF reports) spread their header over two rows, the second
    naming sub-columns under a merged primary header::

        row 7  |   | ACO-Specific | ACO-Specific Stays at Affiliated SNFs |     |         | All MSSP ACOs
        row 8  |   |              | Total                                 | With ... | Without ... |

    Each such sheet is turned into a long/tidy table::

        ROW_NUM | GROUP_LABEL | SECTION_CODE | SECTION_LABEL | ROW_LABEL |
        COLUMN_GROUP_LABEL | COLUMN_LABEL | VALUE_TEXT | ...file metadata

    The "Parameters" tab is the one sheet with no header row at all — column A
    is a key, column B its value, and rows with a blank column B are section
    headers. SheetDef.synthetic_column_labels covers it: header detection is
    skipped, data starts at row 1, and the supplied labels name columns B onward.

    Values are emitted as VARCHAR verbatim ('-' stays '-'); all typing and
    casting happens downstream in dbt.

    Subclasses supply SHEET_DEFS and implement _list_source_file_paths(); they
    may also override _file_metadata_sql() to add report-specific metadata
    columns (performance year, quarter, ...).

    Caveat on ROW_NUM: it is the 1-based position of the row as rusty_sheet
    returns it. rusty_sheet drops *leading* blank rows even with
    skip_empty_rows=false, so on a sheet whose content does not start at
    spreadsheet row 1 the two diverge. Interior blank rows are preserved, and
    every CMS sheet seen to date starts at row 1.
    """

    # Concrete subclasses (BNMRK/AEXPU/QEXPU/STLMT) override this.
    SHEET_DEFS: List[SheetDef] = []

    # Emit the layout grammar the settlement workbook needs (TUVA-167):
    #
    #   BLOCK_LABEL  the title of the block a row sits in, on a SheetDef with
    #                blocks=True (settlement Table A2); NULL elsewhere.
    #   ROW_KIND     'CODE'   the lettered row itself ('[I] Updated Benchmark');
    #                'DETAIL' a row under a lettered or group heading (an
    #                         enrollment type, a grid row, a parameter);
    #                'TOTAL'  an unlettered row repeating its section's label,
    #                         which closes the section with its total.
    #   COLUMN_CODE  the letter closing a header label ('... [I]' -> 'I'),
    #                which is cut from COLUMN_LABEL; NULL elsewhere.
    #
    # A lettered row with no values is kept (its cells NULL) unless it heads
    # detail rows, in which case it is only a heading. Off by default, so the
    # BNMRK / AEXPU / QEXPU output is unchanged.
    LAYOUT_COLUMNS: bool = False

    def __init__(self, session, exporter, config):
        super().__init__(session, exporter, config)
        # {xlsx_path: [sheet name, ...]}. Sheet discovery runs once per
        # (file, SheetDef), and a BNMRK workbook has ten defs, so without this
        # every delivery is opened and its directory parsed ten times. That is
        # tens of seconds locally and materially worse over S3/ADLS.
        self._sheet_names_cache: Dict[str, List[str]] = {}

    def _get_file_definitions(self) -> List[SheetDef]:
        return self.SHEET_DEFS

    def _get_table_name(self, file_def: SheetDef) -> str:
        return file_def.table_name

    # ------------------------------------------------------------------
    # Hooks
    # ------------------------------------------------------------------

    def _file_metadata_sql(self, xlsx_path: str) -> Dict[str, str]:
        """Return {COLUMN_NAME: sql_expression} appended to every output row.

        Base implementation returns FILE_PATH, DIRECTORY_NAME, FILE_NAME as
        string literals and FILE_DATE as NULL::DATE. Subclasses extend/override.

        FILE_PATH is emitted with backslashes normalised to forward slashes.
        Subclasses MUST return that same normalised form as the file_path half of
        _list_source_file_paths() — incremental dedup compares the two, so any
        divergence silently re-appends every source file on every run.
        """
        path_norm = xlsx_path.replace("\\", "/")
        dir_name = path_norm.rsplit("/", 1)[0]
        file_name = path_norm.rsplit("/", 1)[-1]
        return {
            "FILE_PATH": sql_string_literal(path_norm),
            "DIRECTORY_NAME": sql_string_literal(dir_name),
            "FILE_NAME": sql_string_literal(file_name),
            "FILE_DATE": "NULL::DATE",
        }

    # ------------------------------------------------------------------
    # Sheet discovery
    # ------------------------------------------------------------------

    def _discover_sheet(self, xlsx_path: str, sheet_pattern: str) -> Optional[str]:
        """Return the one sheet name matching sheet_pattern, or None if absent.

        Not every delivery carries every sheet (BNMRK "Table 6 - ACPT" is only
        present for ACOs on the ACPT methodology), so a miss is not an error
        here; _build_query() fails the table unless the SheetDef is optional.
        An ambiguous pattern is: 'Table 1%' also matches 'Table 1A - ...' and
        'Table 1B - ...', and silently reading the wrong sheet is far worse than
        failing, so multiple matches raise.

        The LIKE is evaluated with ESCAPE '\\' so that a sheet name containing a
        literal underscore ('Table_1-Aggregate_EU_Report') can be pinned down as
        'Table\\_1-%' instead of relying on '_' the single-character wildcard.
        """
        matches = self.session.connection.execute(f"""
            SELECT sheet_name
            FROM (SELECT UNNEST({_sql_string_list(self._sheet_names(xlsx_path))}) AS sheet_name)
            WHERE sheet_name LIKE {sql_string_literal(sheet_pattern)} ESCAPE '\\'
            ORDER BY sheet_name
        """).fetchall()
        if not matches:
            return None
        if len(matches) > 1:
            candidates = ", ".join(repr(row[0]) for row in matches)
            raise RuntimeError(
                f"Sheet pattern {sheet_pattern!r} is ambiguous in {xlsx_path}: it "
                f"matches {len(matches)} sheets ({candidates}). Narrow the pattern "
                f"so it selects exactly one sheet."
            )
        return matches[0][0]

    def _sheet_names(self, xlsx_path: str) -> List[str]:
        """The workbook's sheet names, read once per path per processor run.

        A read error is deliberately not cached: _build_query() relies on the
        duckdb.Error propagating so it can report the file as unopenable.

        The names come from analyze_sheets(), which inspects each sheet on its
        own and reads no cell data. A combined read_sheets() over the workbook
        must not be used for this: it skips as many leading rows on every later
        sheet as the first sheet's content starts below row 1, and a sheet no
        taller than that drops out of the result entirely. On a real settlement
        workbook that silently lost Tables 1C, A1 and A5 (TUVA-167).
        union_by_name=true avoids the drop but raises when two sheets infer
        different types for a column of the same name.
        """
        if xlsx_path not in self._sheet_names_cache:
            self._sheet_names_cache[xlsx_path] = [
                row[0]
                for row in self.session.connection.execute(f"""
                    SELECT DISTINCT sheet_name
                    FROM analyze_sheets([{sql_string_literal(xlsx_path)}])
                """).fetchall()
            ]
        return self._sheet_names_cache[xlsx_path]

    # ------------------------------------------------------------------
    # Query builder
    # ------------------------------------------------------------------

    def _build_query(self, file_def: SheetDef, source_paths: List[str]) -> str:
        """Materialise every matching sheet in this batch into one tidy temp table.

        A temp table is used (rather than a single giant expression) because the
        header rows and their column labels have to be read from each workbook
        before the unpivot SQL for that workbook can be written.

        run() calls this once per *batch*, so for an optional sheet "no matching
        sheet" is normal: the Table-6-less BNMRK deliveries may well fill a
        whole batch. A required sheet missing from any file raises. Such a batch
        returns a zero-row query of the right shape rather than failing the table.
        Only a batch in which every file failed to open is an error.

        The returned query reads from a temp table that the caller's export step
        owns; it is dropped up front on the next call and on every failure path,
        so at most one batch's worth of rows is ever held.
        """
        print(f"  reading {len(source_paths)} source file(s)")
        conn = self.session.connection
        parts: List[str] = []
        raw_tables: List[str] = []
        open_failures: List[str] = []
        out = validate_identifier("_sectioned_sheet_out", field_name="temp table name")
        conn.execute(f"DROP TABLE IF EXISTS {out}")

        try:
            for i, xlsx_path in enumerate(source_paths):
                try:
                    exact_sheet = self._discover_sheet(xlsx_path, file_def.sheet_pattern)
                except duckdb.Error as e:
                    print(f"  Warning: could not read {xlsx_path}: {e}")
                    open_failures.append(f"{xlsx_path}: {e}")
                    continue
                if not exact_sheet:
                    if not file_def.optional:
                        raise RuntimeError(
                            f"Required sheet {file_def.sheet_pattern!r} is missing "
                            f"from {xlsx_path} ({file_def.table_name}). Only a "
                            f"SheetDef marked optional may be absent."
                        )
                    continue

                raw = validate_identifier(
                    f"_sectioned_raw_{i}", field_name="temp table name"
                )
                conn.execute(f"""
                    CREATE OR REPLACE TEMP TABLE {raw} AS
                    SELECT ROW_NUMBER() OVER () AS ROW_NUM, *
                    FROM read_sheets([{sql_string_literal(xlsx_path)}],
                                     sheets=[{sql_string_literal(exact_sheet)}],
                                     header=false, skip_empty_rows=false)
                """)
                raw_tables.append(raw)
                parts.append(
                    self._build_sheet_select(raw, file_def, xlsx_path, exact_sheet)
                )

            if not parts:
                if open_failures and len(open_failures) == len(source_paths):
                    raise RuntimeError(
                        f"Every source file in this batch failed to open for "
                        f"{file_def.table_name}: {'; '.join(open_failures)}"
                    )
                print(
                    f"  No sheet matching {file_def.sheet_pattern!r} in this batch — "
                    f"contributing 0 rows."
                )
                return self._empty_batch_query(source_paths[0])

            conn.execute(f"""
                CREATE OR REPLACE TEMP TABLE {out} AS
                {' UNION ALL BY NAME '.join(parts)}
            """)
        except BaseException:
            # The caller never gets to consume the output table, so clean it up.
            conn.execute(f"DROP TABLE IF EXISTS {out}")
            raise
        finally:
            for raw in raw_tables:
                conn.execute(f"DROP TABLE IF EXISTS {raw}")

        return f"SELECT * FROM {out}"

    def _empty_batch_query(self, xlsx_path: str) -> str:
        """A zero-row SELECT with exactly the output schema of a real batch."""
        grammar = ", ".join(
            f"CAST(NULL AS {sql_type}) AS {name}"
            for name, sql_type in self._output_columns()
        )
        metadata = "".join(
            f", {expr} AS {validate_identifier(name, field_name='metadata column')}"
            for name, expr in self._file_metadata_sql(xlsx_path).items()
        )
        return f"SELECT {grammar}{metadata} WHERE FALSE"

    def _output_columns(self) -> List[Tuple[str, str]]:
        return _LAYOUT_OUTPUT_COLUMNS if self.LAYOUT_COLUMNS else _OUTPUT_COLUMNS

    # ------------------------------------------------------------------
    # Per-sheet SQL
    # ------------------------------------------------------------------

    def _build_sheet_select(
        self, raw: str, file_def: SheetDef, xlsx_path: str, exact_sheet: str
    ) -> str:
        """Return the SELECT that reshapes one materialised sheet."""
        conn = self.session.connection

        columns = [
            validate_identifier(r[0], field_name="column name")
            for r in conn.execute(f"DESCRIBE {raw}").fetchall()
            if r[0] != "ROW_NUM"
        ]
        label_col = columns[0] if columns else "A"
        value_cols = columns[1:]

        skip_notes_scan = False
        if file_def.synthetic_column_labels is not None:
            retained = _synthetic_header(value_cols, file_def.synthetic_column_labels)
            if not retained:
                raise self._no_header_error(file_def, xlsx_path, exact_sheet)
            # Row 1 is already data: there is no header row to skip past.
            last_header_row = 0
            # A trailing notes block is a sectioned-matrix concept: the block
            # sits below the grid and everything from it down is commentary. A
            # key/value sheet has no grid, and a row whose key merely starts
            # 'Note' is an ordinary parameter — the real AEXPU Parameters tab
            # has one two thirds of the way down. Truncating there would drop
            # every pair beneath it, so the cutoff is disabled here. The
            # '^[n]' footnote-definition filter still applies: those are
            # unambiguous and appear in key/value sheets too.
            skip_notes_scan = True
        else:
            header_row = (
                self._find_header_row(raw, value_cols, file_def) if value_cols else None
            )
            if header_row is None:
                raise self._no_header_error(file_def, xlsx_path, exact_sheet)

            primary = self._row_labels(raw, value_cols, header_row)
            continuation = self._continuation_labels(
                raw, label_col, value_cols, header_row, primary
            )
            retained = _merge_header_rows(value_cols, primary, continuation)
            if not retained:
                raise self._no_header_error(file_def, xlsx_path, exact_sheet)

            last_header_row = header_row + 1 if continuation is not None else header_row
            layout = self._sheet_layout(
                raw, label_col, value_cols, primary, header_row, file_def
            )
            if layout.label_column_header is not None:
                retained = [(label_col, None, layout.label_column_header), *retained]

        if file_def.synthetic_column_labels is not None:
            layout = _SheetLayout()

        notes_row = None
        if not file_def.include_notes and not skip_notes_scan:
            notes_row = self._find_notes_row(raw, label_col, last_header_row)

        return self._render_select(
            raw, label_col, retained, last_header_row, notes_row, file_def,
            xlsx_path, layout,
        )

    def _sheet_layout(
        self,
        raw: str,
        label_col: str,
        value_cols: List[str],
        primary: Sequence[Optional[str]],
        header_row: int,
        file_def: SheetDef,
    ) -> _SheetLayout:
        """Read what label_column_is_data and blocks need from the header."""
        if not (file_def.label_column_is_data or file_def.blocks):
            return _SheetLayout()

        def column_a(row_num: int) -> Optional[str]:
            row = self.session.connection.execute(
                f"SELECT {label_col} FROM {raw} WHERE ROW_NUM = {int(row_num)}"
            ).fetchone()
            return _header_label(row[0]) if row else None

        header_a = column_a(header_row)
        if file_def.label_column_is_data:
            if header_a is None:
                raise RuntimeError(
                    f"{file_def.table_name}: label_column_is_data needs a header "
                    f"label in column A of row {header_row}"
                )
            return _SheetLayout(label_column_header=header_a)
        return _SheetLayout(
            first_block_label=column_a(header_row - 1) if header_row > 1 else None,
            header_cells=((label_col, header_a), *zip(value_cols, primary)),
        )

    def _no_header_error(
        self, file_def: SheetDef, xlsx_path: str, exact_sheet: str
    ) -> RuntimeError:
        if file_def.synthetic_column_labels is not None:
            detail = (
                f"synthetic_column_labels="
                f"{tuple(file_def.synthetic_column_labels)!r} names no column the "
                f"sheet actually has beyond column A"
            )
        elif file_def.header_row_hint is not None:
            detail = (
                f"header_row_hint={file_def.header_row_hint} does not point at a row "
                f"carrying column labels"
            )
        else:
            detail = (
                f"need a row with {file_def.min_header_cells}+ non-blank cells "
                f"beyond column A, or set header_row_hint"
            )
        return RuntimeError(
            f"No header row found in sheet {exact_sheet!r} of {xlsx_path} "
            f"for {file_def.table_name} ({detail})"
        )

    def _find_header_row(
        self, raw: str, value_cols: List[str], file_def: SheetDef
    ) -> Optional[int]:
        """First row with 2+ non-blank cells beyond column A (or the explicit hint).

        The >= 2 threshold matters: BNMRK "Table 6 - ACPT" carries a decorative
        row above the real header holding a single cell ("Performance Year").
        A sheet with only one value column (settlement Table 3) cannot have two;
        its SheetDef lowers min_header_cells to 1. That is opt-in, never inferred
        from the column count: a key/value sheet has one value column too, and
        its first value would be taken for a header.
        Returns None when no header row exists — including when header_row_hint
        falls outside the sheet — so the caller raises one consistent error.
        """
        conn = self.session.connection
        row_count = conn.execute(f"SELECT MAX(ROW_NUM) FROM {raw}").fetchone()[0]
        if row_count is None:
            return None

        if file_def.header_row_hint is not None:
            hint = int(file_def.header_row_hint)
            return hint if 1 <= hint <= int(row_count) else None

        non_blank_count = " + ".join(_non_blank_flag(col) for col in value_cols)
        row = conn.execute(
            f"SELECT MIN(ROW_NUM) FROM {raw} "
            f"WHERE ({non_blank_count}) >= {int(file_def.min_header_cells)}"
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def _row_labels(
        self, raw: str, value_cols: List[str], row_num: int
    ) -> List[Optional[str]]:
        """Normalized header labels of one row (None where blank/missing).

        Only header rows are read through here, so an Excel table's 'Column<n>'
        placeholder counts as blank too.
        """
        values = self.session.connection.execute(
            f"SELECT {', '.join(value_cols)} FROM {raw} WHERE ROW_NUM = {int(row_num)}"
        ).fetchone()
        if values is None:
            return [None] * len(value_cols)
        return [_header_label(value) for value in values]

    def _continuation_labels(
        self,
        raw: str,
        label_col: str,
        value_cols: List[str],
        header_row: int,
        primary: Sequence[Optional[str]],
    ) -> Optional[List[Optional[str]]]:
        """Return the sub-header row's labels, or None if there is no such row.

        The row after the header is a continuation row iff its column A is blank
        AND at least one column pairs a blank primary header with a non-blank
        continuation cell. That second condition is the guard: without it, a data
        row that merely happens to have a blank column A would be consumed as
        column labels.
        """
        row = self.session.connection.execute(
            f"SELECT {label_col} FROM {raw} WHERE ROW_NUM = {int(header_row) + 1}"
        ).fetchone()
        if row is None or _normalize_label(row[0]) is not None:
            return None

        candidate = self._row_labels(raw, value_cols, header_row + 1)
        fills_a_gap = any(
            head is None and sub is not None for head, sub in zip(primary, candidate)
        )
        return candidate if fills_a_gap else None

    def _find_notes_row(self, raw: str, label_col: str, header_row: int) -> Optional[int]:
        """First row after the header block whose column A starts with 'Note'."""
        row = self.session.connection.execute(f"""
            SELECT MIN(ROW_NUM) FROM {raw}
            WHERE ROW_NUM > {int(header_row)}
              AND REGEXP_MATCHES(UPPER({_normalized(label_col)}), '^NOTE')
        """).fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def _render_select(
        self,
        raw: str,
        label_col: str,
        retained: List[Tuple[str, Optional[str], str]],
        header_row: int,
        notes_row: Optional[int],
        file_def: SheetDef,
        xlsx_path: str,
        layout: "_SheetLayout",
    ) -> str:
        """Classify, forward-fill the two-level hierarchy, then unpivot.

        Section state is forward-filled with the running-sum-of-events trick:
        each row that sets a section increments a counter, so FIRST_VALUE over
        that partition returns the setting row's own values. Unlike a plain
        IGNORE NULLS fill this correctly propagates a NULL SECTION_CODE, which
        is what an unbracketed section row must produce. Block titles
        (SheetDef.blocks) are filled the same way.
        """
        label_norm = _normalized(label_col)
        code_expr = (
            f"REGEXP_EXTRACT(CAST({label_col} AS VARCHAR), "
            f"{sql_string_literal(_SECTION_CODE_RE)}, 1)"
        )
        rest_expr = (
            f"REGEXP_EXTRACT(CAST({label_col} AS VARCHAR), "
            f"{sql_string_literal(_SECTION_CODE_RE)}, 2)"
        )
        # Column A can be a value column too (SheetDef.label_column_is_data).
        value_col_list = ", ".join(dict.fromkeys(col for col, _, _ in retained))
        non_blank_count = " + ".join(_non_blank_flag(col) for col, _, _ in retained)

        where = [f"ROW_NUM > {int(header_row)}"]
        if notes_row is not None:
            where.append(f"ROW_NUM < {int(notes_row)}")
        if not file_def.include_notes:
            where.append(
                f"NOT REGEXP_MATCHES(COALESCE(CAST({label_col} AS VARCHAR), ''), "
                f"{sql_string_literal(_FOOTNOTE_ROW_RE)})"
            )
        if layout.header_cells:
            # A later block repeats the header row: it is not data.
            repeat = " AND ".join(
                f"{_normalized(col)} IS NOT DISTINCT FROM {_varchar_literal(label)}"
                for col, label in layout.header_cells
            )
            where.append(f"NOT ({repeat})")

        metadata = self._file_metadata_sql(xlsx_path)
        metadata_sql = "".join(
            f",\n       {expr} AS {validate_identifier(name, field_name='metadata column')}"
            for name, expr in metadata.items()
        )

        columns = [
            (col, group, *(_split_column_code(label) if self.LAYOUT_COLUMNS else (label, None)))
            for col, group, label in retained
        ]
        column_groups = ", ".join(_varchar_literal(c[1]) for c in columns)
        column_labels = ", ".join(_varchar_literal(c[2]) for c in columns)
        column_codes = ", ".join(_varchar_literal(c[3]) for c in columns)
        value_exprs = ", ".join(f"CAST({c[0]} AS VARCHAR)" for c in columns)

        # A bracketed row whose remainder is empty ("[A2]") still deserves a label.
        bracket_label = "COALESCE(_rest_norm, _code)"
        blocks = "TRUE" if file_def.blocks else "FALSE"

        if self.LAYOUT_COLUMNS:
            # A lettered row is emitted, even blank, unless it only heads
            # detail rows; with heading_cells_are_labels it never is when it does.
            emit = "(NOT _is_section OR (_bracketed AND NOT _heads_detail))"
            if file_def.heading_cells_are_labels:
                emit += " AND NOT _heads_detail"
            layout_head = (
                f"COALESCE(BLOCK_LABEL, {_varchar_literal(layout.first_block_label)}) "
                f"AS BLOCK_LABEL,\n                   GROUP_LABEL,"
            )
            row_kind = (
                "CASE WHEN _bracketed THEN 'CODE' "
                "WHEN SECTION_CODE IS NOT NULL AND ROW_LABEL = SECTION_LABEL THEN 'TOTAL' "
                "ELSE 'DETAIL' END AS ROW_KIND,"
            )
            code_col = f"UNNEST([{column_codes}]) AS COLUMN_CODE,"
        else:
            emit = "NOT _is_section"
            layout_head = "GROUP_LABEL,"
            row_kind = ""
            code_col = ""

        return f"""
            SELECT ROW_NUM,
                   {layout_head}
                   SECTION_CODE,
                   SECTION_LABEL,
                   ROW_LABEL,
                   {row_kind}
                   UNNEST([{column_groups}]) AS COLUMN_GROUP_LABEL,
                   {code_col}
                   UNNEST([{column_labels}]) AS COLUMN_LABEL,
                   UNNEST([{value_exprs}]) AS VALUE_TEXT{metadata_sql}
            FROM (
                SELECT ROW_NUM, {value_col_list},
                       CASE WHEN _bracketed THEN {bracket_label}
                            ELSE _label_norm END AS ROW_LABEL,
                       FIRST_VALUE(_event_code)
                           OVER (PARTITION BY _section_grp ORDER BY ROW_NUM) AS SECTION_CODE,
                       FIRST_VALUE(_event_label)
                           OVER (PARTITION BY _section_grp ORDER BY ROW_NUM) AS SECTION_LABEL,
                       FIRST_VALUE(_event_group)
                           OVER (PARTITION BY _group_grp ORDER BY ROW_NUM) AS GROUP_LABEL,
                       FIRST_VALUE(_event_block)
                           OVER (PARTITION BY _block_grp ORDER BY ROW_NUM) AS BLOCK_LABEL,
                       _is_section, _bracketed, _heads_detail
                FROM (
                    SELECT *,
                           SUM(CASE WHEN _is_section OR _bracketed THEN 1 ELSE 0 END)
                               OVER (ORDER BY ROW_NUM
                                     ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                               ) AS _section_grp,
                           SUM(CASE WHEN _is_group THEN 1 ELSE 0 END)
                               OVER (ORDER BY ROW_NUM
                                     ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                               ) AS _group_grp,
                           SUM(CASE WHEN _is_block_title THEN 1 ELSE 0 END)
                               OVER (ORDER BY ROW_NUM
                                     ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                               ) AS _block_grp,
                           _bracketed AND COALESCE(
                               LEAD(NOT _bracketed AND NOT _is_section)
                                   OVER (ORDER BY ROW_NUM),
                               FALSE
                           ) AS _heads_detail,
                           CASE WHEN _bracketed THEN _code
                                WHEN _is_section THEN NULL END AS _event_code,
                           CASE WHEN _bracketed THEN {bracket_label}
                                WHEN _is_section THEN _label_norm END AS _event_label,
                           CASE WHEN _is_group THEN _label_norm END AS _event_group,
                           CASE WHEN _is_block_title THEN _label_norm END AS _event_block
                    FROM (
                        SELECT *,
                               _is_section AND NOT _bracketed AND {blocks} AS _is_block_title,
                               _is_section AND NOT _bracketed AND NOT {blocks} AS _is_group
                        FROM (
                            SELECT ROW_NUM, {value_col_list},
                                   {label_norm} AS _label_norm,
                                   {code_expr} AS _code,
                                   {_normalized(rest_expr)} AS _rest_norm,
                                   {code_expr} <> '' AS _bracketed,
                                   ({non_blank_count}) = 0 AS _is_section
                            FROM {raw}
                            WHERE {' AND '.join(where)}
                        ) _classified
                        WHERE _label_norm IS NOT NULL
                    ) _roles
                ) _flagged
            ) _filled
            WHERE {emit}
        """


# ---------------------------------------------------------------------------
# Header merging
# ---------------------------------------------------------------------------


def _synthetic_header(
    value_cols: Sequence[str], labels: Sequence[str]
) -> List[Tuple[str, Optional[str], str]]:
    """Pair value columns with caller-supplied labels, positionally.

    Used for key/value sheets ("Parameters") that carry no header row at all.
    A column beyond the supplied labels is excluded, exactly as a column with a
    blank header cell is; COLUMN_GROUP_LABEL is always NULL.
    """
    return [
        (col, None, label)
        for col, label in zip(value_cols, [_normalize_label(l) for l in labels])
        if label is not None
    ]


def _merge_header_rows(
    value_cols: Sequence[str],
    primary: Sequence[Optional[str]],
    continuation: Optional[Sequence[Optional[str]]],
) -> List[Tuple[str, Optional[str], str]]:
    """Resolve (column, COLUMN_GROUP_LABEL, COLUMN_LABEL) for each value column.

    With no continuation row this is rule 4 unchanged: blank-header columns are
    dropped and every group label is NULL.

    With one, a blank primary header inherits the nearest non-blank primary to
    its left — Excel reports a cell merged across C:E only in column C — but the
    inherited value is used only where the continuation row actually labels the
    column. A column blank in both header rows is still dropped.
    """
    retained: List[Tuple[str, Optional[str], str]] = []
    carried: Optional[str] = None

    for index, col in enumerate(value_cols):
        head = primary[index] if index < len(primary) else None
        if head is not None:
            carried = head
        sub = None
        if continuation is not None and index < len(continuation):
            sub = continuation[index]

        if sub is not None:
            retained.append((col, carried if head is None else head, sub))
        elif head is not None:
            retained.append((col, None, head))
        # else: blank in both header rows — excluded.

    return retained


# ---------------------------------------------------------------------------
# Label normalization
# ---------------------------------------------------------------------------


def _normalized(expr: str) -> str:
    """SQL expression implementing rule 5, applied to labels only — never values.

    Remove zero-width characters, collapse every run of whitespace to a single
    space, trim, strip a trailing '[n]' footnote marker, trim again, and yield
    NULL when nothing is left.
    Collapsing first is what makes 'With Prior 3-Day  Hospital Stay' joinable
    downstream, and it also lets the plain TRIM below strip leading tabs and
    newlines that DuckDB's TRIM would otherwise leave in place.
    """
    visible = (
        f"REGEXP_REPLACE(CAST({expr} AS VARCHAR), "
        f"{sql_string_literal(_ZERO_WIDTH_RE)}, '', 'g')"
    )
    collapsed = (
        f"REGEXP_REPLACE({visible}, {sql_string_literal(_WHITESPACE_RE)}, ' ', 'g')"
    )
    return (
        f"NULLIF(TRIM(REGEXP_REPLACE(TRIM({collapsed}), "
        f"{sql_string_literal(_FOOTNOTE_SUFFIX_RE)}, '')), '')"
    )


def _normalize_label(value) -> Optional[str]:
    """Python twin of _normalized(), used for header-row column labels.

    The two must agree exactly: header labels are normalized here and emitted as
    literals, while row and section labels are normalized in SQL.
    """
    if value is None:
        return None
    text = _PY_ZERO_WIDTH_RE.sub("", str(value))
    text = _PY_WHITESPACE_RE.sub(" ", text).strip()
    text = _PY_FOOTNOTE_SUFFIX_RE.sub("", text).strip()
    return text or None


def _split_column_code(label: str) -> Tuple[str, Optional[str]]:
    """('Final Risk Ratio (Capped) [I]') -> ('Final Risk Ratio (Capped)', 'I')."""
    match = _PY_COLUMN_CODE_RE.match(label)
    return (match.group(1), match.group(2)) if match else (label, None)


def _header_label(value) -> Optional[str]:
    """A normalized header cell, with Excel's 'Column<n>' placeholder as blank."""
    label = _normalize_label(value)
    if label is not None and _PY_PLACEHOLDER_HEADER_RE.fullmatch(label):
        return None
    return label


def _sql_string_list(values: Sequence[str]) -> str:
    """A DuckDB VARCHAR[] literal — explicitly typed so an empty one still is."""
    if not values:
        return "CAST([] AS VARCHAR[])"
    return "[" + ", ".join(sql_string_literal(v) for v in values) + "]"


def _varchar_literal(value: Optional[str]) -> str:
    """A VARCHAR-typed SQL literal — NULLs are cast so all-NULL lists stay VARCHAR."""
    return "CAST(NULL AS VARCHAR)" if value is None else sql_string_literal(value)


def _non_blank_flag(col: str) -> str:
    """SQL expression yielding 1 when the column holds a non-whitespace value."""
    return f"CASE WHEN TRIM(COALESCE(CAST({col} AS VARCHAR), '')) <> '' THEN 1 ELSE 0 END"
