from typing import List, Tuple

from .base import FileProcessor
from ..defs.bnex_mbi_xref_file_defs import BNEXMBIXrefFileDef, BNEX_MBI_XREF_FILE_DEFS
from ..sql import sql_string_literal


class BNEXMBIXrefProcessor(FileProcessor):
    def _get_file_definitions(self) -> List[BNEXMBIXrefFileDef]:
        return BNEX_MBI_XREF_FILE_DEFS

    def _get_table_name(self, file_def: BNEXMBIXrefFileDef) -> str:
        return file_def.table_name

    def _glob_pattern(self, file_def: BNEXMBIXrefFileDef) -> str:
        return (
            f"{self.config.FILE_STORE}/{self.config.ACO_ID}/"
            f"*/*/P.{self.config.ACO_ID}*{file_def.filename_pattern}*"
        )

    def _list_source_file_paths(self, file_def: BNEXMBIXrefFileDef) -> List[Tuple[str, str]]:
        pattern = self._glob_pattern(file_def)
        try:
            rows = self.session.connection.execute(
                f"SELECT * FROM glob({sql_string_literal(pattern)})"
            ).fetchall()
        except Exception:
            return []
        return [(r[0], r[0]) for r in rows]

    def _build_query(self, file_def: BNEXMBIXrefFileDef, source_paths: List[str]) -> str:
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

        file_date_expr = r"try_strptime('20' || regexp_extract(filename, '\.D(\d{6})\.', 1), '%Y%m%d')::DATE"

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
    return "[" + ", ".join(sql_string_literal(p) for p in paths) + "]"
