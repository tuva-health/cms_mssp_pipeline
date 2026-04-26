from typing import List, Tuple

import duckdb

from .base import FileProcessor
from ..defs.participant_list_defs import ParticipantListFileDef, PARTICIPANT_LIST_FILE_DEFS
from ..sql import sql_string_literal, validate_identifier


class ParticipantListProcessor(FileProcessor):
    """
    Processes Participant List report files

    Files are CSVs in subdirectory hierarchies (not inside zip archives).
    Column names are discovered at runtime via DESCRIBE so that varying schemas
    across delivery bundles are handled automatically. All column names are
    normalized to uppercase. FILE_DATE defaults to today().
    """

    def _get_file_definitions(self) -> List[ParticipantListFileDef]:
        return PARTICIPANT_LIST_FILE_DEFS

    def _get_table_name(self, file_def: ParticipantListFileDef) -> str:
        return file_def.table_name

    def _glob_pattern(self, file_def: ParticipantListFileDef) -> str:
        return (
            f"{self.config.FILE_STORE}/{self.config.ACO_ID}/"
            f"*/*/{file_def.filename_pattern}"
        )

    def _list_source_file_paths(self, file_def: ParticipantListFileDef) -> List[Tuple[str, str]]:
        pattern = self._glob_pattern(file_def)
        try:
            rows = self.session.connection.execute(
                f"SELECT * FROM glob({sql_string_literal(pattern)})"
            ).fetchall()
        except duckdb.IOException as e:
            print(f"  Warning: could not list participant list source files (pattern={pattern}): {e}")
            return []
        return [(r[0], r[0]) for r in rows]

    def _build_query(self, file_def: ParticipantListFileDef, source_paths: List[str]) -> str:
        paths_sql = _sql_path_list(source_paths)
        print(f"  reading {len(source_paths)} source file(s)")

        # Discover column names from the files so we can uppercase them.
        cols = self.session.connection.execute(f"""
            DESCRIBE SELECT * FROM read_csv({paths_sql},
                header=true,
                auto_detect=true,
                auto_type_candidates = ['VARCHAR'],
                union_by_name=true,
                normalize_names=True,
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
                          normalize_names=True,
                          ignore_errors=true)
        """


def _sql_path_list(paths: List[str]) -> str:
    """Format a list of file paths as a DuckDB list literal: ['path1', 'path2']."""
    return "[" + ", ".join(sql_string_literal(p) for p in paths) + "]"
