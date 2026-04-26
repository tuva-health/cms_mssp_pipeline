from typing import List, Tuple

import duckdb

from .base import FileProcessor
from ..defs.mssp_file_defs import MSSPFileDef, MSSP_FILE_DEFS
from ..sql import sql_string_literal, validate_identifier


class MSSPProcessor(FileProcessor):
    """
    Processes MSSP report files (ALR, BEUR, NCBP, BAIP, etc.).

    Files are CSVs embedded in zip archives (no .zip extension) located inside
    year and bundle subdirectories:
        FILE_STORE/ACO_ID/YEAR/P.ACO_ID.ACO.BUNDLE.DATE.TIME/P.ACO_ID.ACO.REPORT.DATE.TIME!CSV

    Column names are discovered at runtime via DESCRIBE so that varying schemas
    across delivery bundles are handled automatically. All column names are
    normalized to uppercase. FILE_DATE defaults to today() since MSSP filenames
    carry no date component.
    """

    def _get_file_definitions(self) -> List[MSSPFileDef]:
        return MSSP_FILE_DEFS

    def _get_table_name(self, file_def: MSSPFileDef) -> str:
        return file_def.table_name

    def _glob_pattern(self, file_def: MSSPFileDef) -> str:
        type_prefix = file_def.filename_pattern.split("*")[0]
        return (
            f"zip://{self.config.FILE_STORE}/{self.config.ACO_ID}/"
            f"*/*/P.{self.config.ACO_ID}.ACO*/"
            f"*{type_prefix}*T*!*{file_def.filename_pattern}"
        )

    def _list_source_file_paths(self, file_def: MSSPFileDef) -> List[Tuple[str, str]]:
        pattern = self._glob_pattern(file_def)
        try:
            rows = self.session.connection.execute(
                f"SELECT * FROM glob({sql_string_literal(pattern)})"
            ).fetchall()
        except duckdb.IOException as e:
            print(f"  Warning: could not list MSSP source files (pattern={pattern}): {e}")
            return []
        # source_path is the zipfs reference (zip://...!...).
        # FILE_PATH strips 'zip://' and removes the '!' separator, matching
        # what _metadata_columns() produces at query time.
        return [
            (r[0].replace("zip://", "").replace("!", ""), r[0])
            for r in rows
        ]

    def _build_query(self, file_def: MSSPFileDef, source_paths: List[str]) -> str:
        paths_sql = _sql_path_list(source_paths)
        print(f"  reading {len(source_paths)} source file(s)")

        # Discover column names from the files so we can uppercase them.
        cols = self.session.connection.execute(f"""
            DESCRIBE SELECT * FROM read_csv({paths_sql},
                header=true,
                auto_detect=true,
                auto_type_candidates = ['VARCHAR'],
                union_by_name=true,
                ignore_errors=true)
        """).fetchall()

        select_clause = ", ".join([
            f"{validate_identifier(c[0], field_name='column name')} AS {validate_identifier(c[0].upper(), field_name='column alias')}"
            for c in cols
        ])

        return f"""
            SELECT
                {select_clause},
                {self._metadata_columns("today()")}
            FROM read_csv({paths_sql},
                          header=true,
                          auto_detect=true,
                          auto_type_candidates = ['VARCHAR'],
                          filename=true,
                          union_by_name=true,
                          ignore_errors=true)
        """


def _sql_path_list(paths: List[str]) -> str:
    """Format a list of file paths as a DuckDB list literal: ['path1', 'path2']."""
    return "[" + ", ".join(sql_string_literal(p) for p in paths) + "]"
