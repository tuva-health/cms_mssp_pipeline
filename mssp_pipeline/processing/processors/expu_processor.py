import re
from datetime import date, timedelta
from typing import List, Optional, Tuple

import duckdb

from .base import FileProcessor
from ..exceptions import SourceDiscoveryError, is_empty_glob
from ..defs.expu_file_defs import EXPUFileDef, EXPU_FILE_DEFS
from ..sql import sql_string_literal, validate_identifier


class EXPUProcessor(FileProcessor):
    """
    Processes QEXPU (Quarterly Expenditure & Utilization) files.

    Files are xlsx workbooks delivered directly into year/bundle directories:
        FILE_STORE/ACO_ID/YEAR/P.ACO_ID.ACO.QEXPU.YYYYQ#.D.../P.ACO_ID*EXPU*.xlsx

    Three output tables correspond to the three Table_* sheets in each workbook:
        EXPU_TABLE_1 <- sheets starting with 'Table_1'  (add_section_column=True)
        EXPU_TABLE_2 <- sheets starting with 'Table_2'  (unpivot_periods=True)
        EXPU_TABLE_3 <- sheets starting with 'Table_3'  (add_section_column=True)

    Sheet names vary across quarterly deliveries (e.g. 'Table_1-Aggregate_EU_Report')
    so each file is inspected via rusty_sheet to find the exact name matching the
    configured prefix. rusty_sheet's read_sheets() handles local, S3, and ADLS paths
    using DuckDB's pre-configured credential context.

    Tables 1 & 3 — section header propagation:
        Rows where column A is non-empty and columns B, C, D are all null/empty are
        treated as section-header rows. Their A value is propagated as a SECTION column
        to all subsequent rows in that section, making each data row self-describing.

    Table 2 — period unpivot:
        Row 7 (after 6 title rows) contains column-header labels such as
        "Benchmark Year 2024", "Q1 2025". All columns except A are period value
        columns. The table is unpivoted to (A, PERIOD_COLUMN, VALUE) — one row per
        (row-label, period) — with period labels read dynamically from row 7.

    FILE_DATE = last day of the quarter from the YYYYQ# segment in the filename
    (e.g. 2025Q1 -> 2025-03-31). PERIOD = the literal quarter string (e.g. '2025Q1').
    Both are computed from the filename, matching MCQM's convention.
    """

    def _get_file_definitions(self) -> List[EXPUFileDef]:
        return EXPU_FILE_DEFS

    def _get_table_name(self, file_def: EXPUFileDef) -> str:
        return file_def.table_name

    def _find_xlsx_paths(self) -> List[str]:
        """Discover EXPU xlsx paths via DuckDB glob — works for local, S3, and ADLS
        because the session has already loaded and configured the relevant extensions."""
        pattern = (
            f"{self.config.FILE_STORE}/{self.config.ACO_ID}/"
            f"*/*/P.{self.config.ACO_ID}*EXPU*/*EXPU*.xlsx"
        )
        try:
            rows = self.session.connection.execute(
                f"SELECT * FROM glob({sql_string_literal(pattern)})"
            ).fetchall()
        except duckdb.IOException as e:
            if is_empty_glob(e):
                return []
            raise SourceDiscoveryError(
                f"Could not list EXPU source files (pattern={pattern}): {e}"
            ) from e
        return sorted(r[0] for r in rows)

    def _quarter_end_date(self, filename: str) -> Optional[date]:
        """Return the last calendar day of the quarter encoded in the filename."""
        m = re.search(r"\.(\d{4})Q(\d)\.", filename)
        if not m:
            return None
        year, quarter = int(m.group(1)), int(m.group(2))
        end_month = quarter * 3
        if end_month == 12:
            return date(year, 12, 31)
        return date(year, end_month + 1, 1) - timedelta(days=1)

    def _discover_sheet(self, xlsx_path: str, sheet_prefix: str) -> Optional[str]:
        """Return the exact sheet name matching sheet_prefix, or None if not found."""
        row = self.session.connection.execute(f"""
            SELECT DISTINCT sheet_name
            FROM read_sheets([{sql_string_literal(xlsx_path)}], sheet_name_column='sheet_name')
            WHERE sheet_name LIKE {sql_string_literal(f'{sheet_prefix}%')}
            LIMIT 1
        """).fetchone()
        return row[0] if row else None

    def _file_metadata(self, xlsx_path: str):
        """Return (path_norm, dir_name, file_name, period, file_date_sql) for a path."""
        path_norm = xlsx_path.replace("\\", "/")
        dir_name = path_norm.rsplit("/", 1)[0]
        file_name = path_norm.rsplit("/", 1)[-1]
        period_m = re.search(r"\.(\d{4}Q\d)\.", xlsx_path)
        period = period_m.group(1) if period_m else ""
        file_date_val = self._quarter_end_date(xlsx_path)
        file_date_sql = f"DATE '{file_date_val}'" if file_date_val else "NULL::DATE"
        return path_norm, dir_name, file_name, period, file_date_sql

    def _list_source_file_paths(self, file_def: EXPUFileDef) -> List[Tuple[str, str]]:
        """Enumerate (file_path, source_path) for every EXPU xlsx.

        FILE_PATH is the normalized xlsx path (matches _file_metadata() output).
        source_path is the raw glob result (used for read_sheets / query building).
        """
        xlsx_paths = self._find_xlsx_paths()
        result = []
        for xlsx_path in xlsx_paths:
            path_norm = xlsx_path.replace("\\", "/")
            result.append((path_norm, xlsx_path))
        return result

    # ------------------------------------------------------------------
    # Query builders
    # ------------------------------------------------------------------

    def _build_query(self, file_def: EXPUFileDef, source_paths: List[str]) -> str:
        print(f"  reading {len(source_paths)} source file(s)")
        if file_def.unpivot_periods:
            return self._build_unpivot_query(source_paths, file_def)
        elif file_def.add_section_column:
            return self._build_section_query(source_paths, file_def)
        else:
            return self._build_standard_query(source_paths, file_def)

    def _build_standard_query(self, xlsx_paths: List[str], file_def: EXPUFileDef) -> str:
        """Read all rows verbatim — no structural transformation."""
        selects = []
        for xlsx_path in xlsx_paths:
            exact_sheet = self._discover_sheet(xlsx_path, file_def.sheet_prefix)
            if not exact_sheet:
                continue

            _, dir_name, file_name, period, file_date_sql = self._file_metadata(xlsx_path)

            selects.append(f"""
                SELECT * EXCLUDE (sheet_name, filename),
                       {sql_string_literal(xlsx_path)} AS FILE_PATH,
                       {sql_string_literal(dir_name)}  AS DIRECTORY_NAME,
                       {sql_string_literal(file_name)} AS FILE_NAME,
                       {file_date_sql} AS FILE_DATE,
                       {sql_string_literal(period)}    AS PERIOD
                FROM read_sheets([{sql_string_literal(xlsx_path)}], sheets=[{sql_string_literal(exact_sheet)}],
                                 header=false, skip_empty_rows=false,
                                 file_name_column='filename',
                                 sheet_name_column='sheet_name')
            """)

        if not selects:
            raise RuntimeError(
                f"Sheet prefix '{file_def.sheet_prefix}' not found in any EXPU xlsx"
            )
        return " UNION ALL BY NAME ".join(selects)

    def _build_section_query(self, xlsx_paths: List[str], file_def: EXPUFileDef) -> str:
        """Read all rows and add a SECTION column propagated from section-header rows.

        A section-header row is one where column A is non-empty AND columns B, C, D
        are all NULL or empty string. The SECTION value from the most recent header
        above is carried forward to every subsequent row via window functions.
        """
        _is_header = (
            "A IS NOT NULL"
            " AND TRIM(CAST(A AS VARCHAR)) <> ''"
            " AND (B IS NULL OR TRIM(CAST(B AS VARCHAR)) = '')"
            " AND (C IS NULL OR TRIM(CAST(C AS VARCHAR)) = '')"
            " AND (D IS NULL OR TRIM(CAST(D AS VARCHAR)) = '')"
        )

        selects = []
        for xlsx_path in xlsx_paths:
            exact_sheet = self._discover_sheet(xlsx_path, file_def.sheet_prefix)
            if not exact_sheet:
                continue

            _, dir_name, file_name, period, file_date_sql = self._file_metadata(xlsx_path)

            selects.append(f"""
                SELECT * EXCLUDE (_rn, _section_flag, _group_id, sheet_name, filename),
                       FIRST_VALUE(_section_flag IGNORE NULLS)
                           OVER (PARTITION BY _group_id ORDER BY _rn) AS SECTION,
                       {sql_string_literal(xlsx_path)} AS FILE_PATH,
                       {sql_string_literal(dir_name)}  AS DIRECTORY_NAME,
                       {sql_string_literal(file_name)} AS FILE_NAME,
                       {file_date_sql} AS FILE_DATE,
                       {sql_string_literal(period)}    AS PERIOD
                FROM (
                    SELECT *,
                           CASE WHEN {_is_header}
                                THEN TRIM(CAST(A AS VARCHAR)) END AS _section_flag,
                           SUM(CASE WHEN {_is_header} THEN 1 ELSE 0 END)
                               OVER (ORDER BY _rn
                                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                               ) AS _group_id
                    FROM (
                        SELECT ROW_NUMBER() OVER () AS _rn, *
                        FROM read_sheets([{sql_string_literal(xlsx_path)}], sheets=[{sql_string_literal(exact_sheet)}],
                                         header=false, skip_empty_rows=false,
                                         file_name_column='filename',
                                         sheet_name_column='sheet_name')
                    ) _inner
                ) _outer
            """)

        if not selects:
            raise RuntimeError(
                f"Sheet prefix '{file_def.sheet_prefix}' not found in any EXPU xlsx"
            )
        return " UNION ALL BY NAME ".join(selects)

    def _build_unpivot_query(self, xlsx_paths: List[str], file_def: EXPUFileDef) -> str:
        """Unpivot Table 2 period columns to (A, PERIOD_COLUMN, VALUE) rows.

        Row 7 of the sheet holds the period-column labels (e.g. "Benchmark Year 2024",
        "Q1 2025"). All columns except A are treated as period value columns and are
        unpivoted. A temp table is materialised so the returned query is a simple SELECT.
        Only the provided xlsx_paths (new files) are materialised — already-loaded files
        are not touched.
        """
        conn = self.session.connection
        all_parts = []

        for i, xlsx_path in enumerate(xlsx_paths):
            exact_sheet = self._discover_sheet(xlsx_path, file_def.sheet_prefix)
            if not exact_sheet:
                continue

            _, dir_name, file_name, period, file_date_sql = self._file_metadata(xlsx_path)

            # Load the sheet with row numbers into a temp table
            tmp = validate_identifier(f"_expu_t2_raw_{i}", field_name="temp table name")
            conn.execute(f"""
                CREATE OR REPLACE TEMP TABLE {tmp} AS
                SELECT ROW_NUMBER() OVER () AS _rn, *
                FROM read_sheets([{sql_string_literal(xlsx_path)}], sheets=[{sql_string_literal(exact_sheet)}],
                                 header=false, skip_empty_rows=false,
                                 file_name_column='filename',
                                 sheet_name_column='sheet_name')
            """)

            # Determine period value columns (all except _rn, A, filename, sheet_name)
            col_names = [
                validate_identifier(r[0], field_name="column name")
                for r in conn.execute(f"DESCRIBE {tmp}").fetchall()
                if r[0] not in ("_rn", "A", "filename", "sheet_name")
            ]
            if not col_names:
                continue

            # Read period labels from row 7 (column-header row after 6 title rows)
            header_vals = conn.execute(
                f"SELECT {', '.join(col_names)} FROM {tmp} WHERE _rn = 7"
            ).fetchone()

            # Build one SELECT per period column (data rows only: _rn > 7)
            for col, label in zip(col_names, header_vals):
                label_value = str(label) if label is not None else col
                all_parts.append(f"""
                    SELECT CAST(A AS VARCHAR)       AS A,
                           {sql_string_literal(label_value)} AS PERIOD_COLUMN,
                           CAST({col} AS VARCHAR)   AS VALUE,
                           {sql_string_literal(xlsx_path)} AS FILE_PATH,
                           {sql_string_literal(dir_name)}  AS DIRECTORY_NAME,
                           {sql_string_literal(file_name)} AS FILE_NAME,
                           {file_date_sql}          AS FILE_DATE,
                           {sql_string_literal(period)}    AS PERIOD
                    FROM {tmp}
                    WHERE _rn > 7
                """)

        if not all_parts:
            raise RuntimeError(
                f"Sheet prefix '{file_def.sheet_prefix}' not found in any EXPU xlsx"
            )

        temp_table = validate_identifier("_expu_t2_unpivoted", field_name="temp table name")
        conn.execute(f"""
            CREATE OR REPLACE TEMP TABLE {temp_table} AS
            {' UNION ALL '.join(all_parts)}
        """)
        return f"SELECT * FROM {temp_table}"
