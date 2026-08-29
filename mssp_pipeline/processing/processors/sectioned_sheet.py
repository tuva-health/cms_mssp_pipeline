import re
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

# A footnote *definition* row, e.g. '[10]See "Parameters" tab for details.'
_FOOTNOTE_ROW_RE = r"^\s*\[\d+\]"

_PY_FOOTNOTE_SUFFIX_RE = re.compile(_FOOTNOTE_SUFFIX_RE)
_PY_WHITESPACE_RE = re.compile(_WHITESPACE_RE)

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

    # Concrete subclasses (BNMRK/AEXPU/QEXPU) override this.
    SHEET_DEFS: List[SheetDef] = []

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
        present for ACOs on the ACPT methodology), so a miss is not an error.
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
        """
        if xlsx_path not in self._sheet_names_cache:
            self._sheet_names_cache[xlsx_path] = [
                row[0]
                for row in self.session.connection.execute(f"""
                    SELECT DISTINCT sheet_name
                    FROM read_sheets([{sql_string_literal(xlsx_path)}],
                                     sheet_name_column='sheet_name')
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

        run() calls this once per *batch*, so "no matching sheet" is normal: the
        Table-6-less BNMRK deliveries may well fill a whole batch. Such a batch
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
            f"CAST(NULL AS {sql_type}) AS {name}" for name, sql_type in _OUTPUT_COLUMNS
        )
        metadata = "".join(
            f", {expr} AS {validate_identifier(name, field_name='metadata column')}"
            for name, expr in self._file_metadata_sql(xlsx_path).items()
        )
        return f"SELECT {grammar}{metadata} WHERE FALSE"

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

        notes_row = None
        if not file_def.include_notes and not skip_notes_scan:
            notes_row = self._find_notes_row(raw, label_col, last_header_row)

        return self._render_select(
            raw, label_col, retained, last_header_row, notes_row, file_def, xlsx_path
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
            detail = "need a row with 2+ non-blank cells beyond column A, or set header_row_hint"
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
            f"SELECT MIN(ROW_NUM) FROM {raw} WHERE ({non_blank_count}) >= 2"
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def _row_labels(
        self, raw: str, value_cols: List[str], row_num: int
    ) -> List[Optional[str]]:
        """Normalized value-column cells of one row (None where blank/missing)."""
        values = self.session.connection.execute(
            f"SELECT {', '.join(value_cols)} FROM {raw} WHERE ROW_NUM = {int(row_num)}"
        ).fetchone()
        if values is None:
            return [None] * len(value_cols)
        return [_normalize_label(value) for value in values]

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
    ) -> str:
        """Classify, forward-fill the two-level hierarchy, then unpivot.

        Section state is forward-filled with the running-sum-of-events trick:
        each row that sets a section increments a counter, so FIRST_VALUE over
        that partition returns the setting row's own values. Unlike a plain
        IGNORE NULLS fill this correctly propagates a NULL SECTION_CODE, which
        is what an unbracketed section row must produce.
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
        value_col_list = ", ".join(col for col, _, _ in retained)
        non_blank_count = " + ".join(_non_blank_flag(col) for col, _, _ in retained)

        where = [f"ROW_NUM > {int(header_row)}"]
        if notes_row is not None:
            where.append(f"ROW_NUM < {int(notes_row)}")
        if not file_def.include_notes:
            where.append(
                f"NOT REGEXP_MATCHES(COALESCE(CAST({label_col} AS VARCHAR), ''), "
                f"{sql_string_literal(_FOOTNOTE_ROW_RE)})"
            )

        metadata = self._file_metadata_sql(xlsx_path)
        metadata_sql = "".join(
            f",\n       {expr} AS {validate_identifier(name, field_name='metadata column')}"
            for name, expr in metadata.items()
        )

        column_groups = ", ".join(_varchar_literal(group) for _, group, _ in retained)
        column_labels = ", ".join(_varchar_literal(label) for _, _, label in retained)
        value_exprs = ", ".join(f"CAST({col} AS VARCHAR)" for col, _, _ in retained)

        # A bracketed row whose remainder is empty ("[A2]") still deserves a label.
        bracket_label = "COALESCE(_rest_norm, _code)"

        return f"""
            SELECT ROW_NUM,
                   GROUP_LABEL,
                   SECTION_CODE,
                   SECTION_LABEL,
                   ROW_LABEL,
                   UNNEST([{column_groups}]) AS COLUMN_GROUP_LABEL,
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
                       _is_section
                FROM (
                    SELECT *,
                           SUM(CASE WHEN _is_section OR _bracketed THEN 1 ELSE 0 END)
                               OVER (ORDER BY ROW_NUM
                                     ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                               ) AS _section_grp,
                           SUM(CASE WHEN _is_section AND NOT _bracketed THEN 1 ELSE 0 END)
                               OVER (ORDER BY ROW_NUM
                                     ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                               ) AS _group_grp,
                           CASE WHEN _bracketed THEN _code
                                WHEN _is_section THEN NULL END AS _event_code,
                           CASE WHEN _bracketed THEN {bracket_label}
                                WHEN _is_section THEN _label_norm END AS _event_label,
                           CASE WHEN _is_section AND NOT _bracketed
                                THEN _label_norm END AS _event_group
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
                ) _flagged
            ) _filled
            WHERE NOT _is_section
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

    Collapse every run of whitespace to a single space, trim, strip a trailing
    '[n]' footnote marker, trim again, and yield NULL when nothing is left.
    Collapsing first is what makes 'With Prior 3-Day  Hospital Stay' joinable
    downstream, and it also lets the plain TRIM below strip leading tabs and
    newlines that DuckDB's TRIM would otherwise leave in place.
    """
    collapsed = (
        f"REGEXP_REPLACE(CAST({expr} AS VARCHAR), "
        f"{sql_string_literal(_WHITESPACE_RE)}, ' ', 'g')"
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
    text = _PY_WHITESPACE_RE.sub(" ", str(value)).strip()
    text = _PY_FOOTNOTE_SUFFIX_RE.sub("", text).strip()
    return text or None


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
