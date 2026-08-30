from typing import List, Tuple

import duckdb

from .base import FileProcessor
from ..exceptions import SourceDiscoveryError, is_empty_glob
from ..defs.bnex_file_defs import BNEXFileDef, BNEX_FILE_DEFS
from ..sql import sql_string_literal

# Regex to extract the 6-digit YYMMDD date component from BNEX filenames.
# Example: P.C1234.BNEX.R25.D260212.T1420360.xml → captures "260212"
_DATE_REGEX = r"\.D(\d{6})\."


class BNEXProcessor(FileProcessor):
    """
    Processes BNEX (Beneficiary Exclusion) XML files.

    Files are raw XML (not zipped) stored in year subdirectories under
    FILE_STORE/ACO_ID/YEAR/. Each file contains a Header, a list of Beneficiary
    elements, and a Trailer.

    Uses the `webbed` DuckDB community extension's read_xml() to parse XML
    natively via SQL — no Python-level XML parsing or TEMP TABLE management.
    read_xml() is called with record_element='PFDCACOBeneData' to produce one
    row per file (preserving Header context), then UNNEST fans out to one row
    per beneficiary.

    FILE_DATE is parsed from the D{YYMMDD} segment in the filename.
    Header fields (HEADERCODE, FILECREATIONDATE, PERFORMANCEYEAR, REPORTMONTH)
    are appended to every beneficiary row.
    """

    def _get_file_definitions(self) -> List[BNEXFileDef]:
        return BNEX_FILE_DEFS

    def _get_table_name(self, file_def: BNEXFileDef) -> str:
        return file_def.table_name

    def _glob_pattern(self, file_def: BNEXFileDef) -> str:
        return (
            f"{self.config.FILE_STORE}/{self.config.ACO_ID}/"
            f"*/*/P.{self.config.ACO_ID}.{file_def.file_pattern}"
        )

    def _list_source_file_paths(self, file_def: BNEXFileDef) -> List[Tuple[str, str]]:
        pattern = self._glob_pattern(file_def)
        try:
            rows = self.session.connection.execute(
                f"SELECT * FROM glob({sql_string_literal(pattern)})"
            ).fetchall()
        except duckdb.IOException as e:
            if is_empty_glob(e):
                return []
            raise SourceDiscoveryError(
                f"Could not list BNEX source files (pattern={pattern}): {e}"
            ) from e
        # FILE_PATH == source_path: read_xml's `filename` column returns the direct path.
        return [(r[0], r[0]) for r in rows]

    def _build_query(self, file_def: BNEXFileDef, source_paths: List[str]) -> str:
        paths_sql = _sql_path_list(source_paths)
        print(f"  reading {len(source_paths)} source file(s)")

        # Note: webbed preserves original XML element casing (MBI, FirstName, etc.)
        # and infers numeric fields (DOB, PerformanceYear, etc.) as INTEGER —
        # all are cast to VARCHAR. UNNEST must be in a subquery SELECT (not the
        # outer FROM clause) for compatibility with the DuckDB version used here.
        return f"""
            SELECT
                bene.MBI::VARCHAR                                                                  AS MBI,
                bene.HICN::VARCHAR                                                                 AS HICN,
                bene.FirstName::VARCHAR                                                            AS FIRSTNAME,
                bene.MiddleName::VARCHAR                                                           AS MIDDLENAME,
                bene.LastName::VARCHAR                                                             AS LASTNAME,
                bene.DOB::VARCHAR                                                                  AS DOB,
                bene.Gender::VARCHAR                                                               AS GENDER,
                list_aggregate(bene.BeneExcReasons.BeneExcReason, 'string_agg', ',')::VARCHAR      AS BENEEXCREASONS,
                src.Header.HeaderCode::VARCHAR                                                     AS HEADERCODE,
                src.Header.FileCreationDate::VARCHAR                                               AS FILECREATIONDATE,
                src.Header.PerformanceYear::VARCHAR                                                AS PERFORMANCEYEAR,
                src.Header.ReportMonth::VARCHAR                                                    AS REPORTMONTH,
                src.filename                                                                       AS FILE_PATH,
                regexp_extract(src.filename, '^(.*)/[^/]+$', 1)                                   AS DIRECTORY_NAME,
                regexp_extract(src.filename, '([^/]+)$', 1)                                       AS FILE_NAME,
                try_strptime('20' || regexp_extract(src.filename, '{_DATE_REGEX}', 1), '%Y%m%d')::DATE AS FILE_DATE
            FROM (
                SELECT UNNEST(Beneficiarys.Beneficiary) AS bene, Header, filename
                FROM read_xml(
                    {paths_sql},
                    record_element := 'PFDCACOBeneData',
                    force_list      := ['Beneficiary', 'BeneExcReason'],
                    filename        := true
                )
            ) src
        """


def _sql_path_list(paths: List[str]) -> str:
    """Format a list of file paths as a DuckDB list literal: ['path1', 'path2']."""
    return "[" + ", ".join(sql_string_literal(p) for p in paths) + "]"
