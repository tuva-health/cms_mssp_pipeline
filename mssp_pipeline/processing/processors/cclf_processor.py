from typing import List, Tuple

from .base import FileProcessor
from ..defs.cclf_file_defs import CCLFFileDef, CCLF_FILE_DEFS


class CCLFProcessor(FileProcessor):
    """
    Processes CCLF (Comprehensive Claim and Line Feed) files.

    Files are fixed-width text files in year/bundle subdirectories. Column
    positions are computed cumulatively from the widths defined in
    CCLFFileDef.columns. FILE_DATE is parsed from the filename using the
    pattern .D<YYMMDD>.
    """

    def _get_file_definitions(self) -> List[CCLFFileDef]:
        return CCLF_FILE_DEFS

    def _get_table_name(self, file_def: CCLFFileDef) -> str:
        return file_def.table_name

    def _match_pattern(self, file_def: CCLFFileDef) -> str:
        return (
            f"{self.config.FILE_STORE}/{self.config.ACO_ID}/*/*/"
            f"P.{self.config.ACO_ID}.ACO*/"
            f"*{file_def.filename_pattern}*"
        )

    def _list_source_file_paths(self, file_def: CCLFFileDef) -> List[Tuple[str, str]]:
        pattern = self._match_pattern(file_def)
        try:
            rows = self.session.connection.execute(
                f"SELECT * FROM glob('{pattern}')"
            ).fetchall()
        except Exception:
            return []
        # FILE_PATH == source_path for plain (non-zip) files.
        return [(r[0], r[0]) for r in rows]

    def _build_query(self, file_def: CCLFFileDef, source_paths: List[str]) -> str:
        index = 1
        select_parts = []
        for col in file_def.columns:
            select_parts.append(
                f"CAST(trim(substring(rawline, {index}, {col.width})) AS VARCHAR) AS {col.name}"
            )
            index += col.width
        select_sql = ",\n                ".join(select_parts)

        paths_sql = _sql_path_list(source_paths)
        print(f"  reading {len(source_paths)} source file(s)")

        file_date_expr = (
            r"try_strptime('20' || regexp_extract(filename, '\.D(\d{6})\.', 1), '%Y%m%d')::DATE"
        )

        return f"""
            SELECT
                {select_sql},
                {self._metadata_columns(file_date_expr)}
            FROM read_csv({paths_sql},
                          header={file_def.has_header},
                          auto_detect=false,
                          auto_type_candidates = ['VARCHAR'],
                          filename=true,
                          ignore_errors=true,
                          columns= {{'rawline': 'VARCHAR'}})
        """


def _sql_path_list(paths: List[str]) -> str:
    """Format a list of file paths as a DuckDB list literal: ['path1', 'path2']."""
    escaped = [p.replace("'", "''") for p in paths]
    return "[" + ", ".join(f"'{p}'" for p in escaped) + "]"
