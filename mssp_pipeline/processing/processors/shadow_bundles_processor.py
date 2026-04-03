from typing import List, Tuple

from .base import FileProcessor
from ..defs.shadow_bundles_defs import ShadowBundleFileDef, SHADOW_BUNDLES_FILE_DEFS


class ShadowBundlesProcessor(FileProcessor):
    """
    Processes Shadow Bundles report files.

    Files are plain CSVs in year/bundle subdirectories under FILE_STORE:
        FILE_STORE/ACO_ID/YEAR/ACO_ID.NN.SBMON.DATE.TIME/DM_ACO_ID_Month_Year.csv

    Column names are discovered at runtime via DESCRIBE so that varying schemas
    across delivery bundles are handled automatically. All column names are
    normalized to uppercase. FILE_DATE defaults to today().
    """

    def _get_file_definitions(self) -> List[ShadowBundleFileDef]:
        return SHADOW_BUNDLES_FILE_DEFS

    def _get_table_name(self, file_def: ShadowBundleFileDef) -> str:
        return file_def.table_name

    def _glob_pattern(self, file_def: ShadowBundleFileDef) -> str:
        return (
            f"{self.config.FILE_STORE}/{self.config.ACO_ID}/"
            f"*/*/{self.config.ACO_ID}*SBMON*/{file_def.filename_pattern}"
        )

    def _list_source_file_paths(self, file_def: ShadowBundleFileDef) -> List[Tuple[str, str]]:
        pattern = self._glob_pattern(file_def)
        try:
            rows = self.session.connection.execute(
                f"SELECT * FROM glob('{pattern}')"
            ).fetchall()
        except Exception:
            return []
        return [(r[0], r[0]) for r in rows]

    def _build_query(self, file_def: ShadowBundleFileDef, source_paths: List[str]) -> str:
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

        select_clause = ", ".join([f"{c[0]} AS {c[0].upper()}" for c in cols])

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
    escaped = [p.replace("'", "''") for p in paths]
    return "[" + ", ".join(f"'{p}'" for p in escaped) + "]"
