import re
from datetime import date, timedelta
from typing import List, Optional, Tuple

import duckdb

from .base import FileProcessor
from ..exceptions import SourceDiscoveryError, is_empty_glob
from ..defs.mcqm_file_defs import MCQMFileDef, MCQM_FILE_DEFS
from ..sql import sql_string_literal


class MCQMProcessor(FileProcessor):
    """
    Processes MCQM (Medicare Clinical Quality Measures) files.

    Through PY2025, files are xlsx workbooks (one worksheet per measure)
    embedded in extension-less zip archives inside year/bundle directories:
        FILE_STORE/ACO_ID/YEAR/P.ACO_ID*/P.ACO_ID.ACO.MCQM.YYYYQN.DATE.TIME

    Starting in PY2026, the MCQM zip is delivered inside the extracted QEXPU
    bundle and contains one CSV per MCQM table:
        FILE_STORE/ACO_ID/YEAR/.../P.ACO_ID.ACO.QEXPU.../
            P.ACO_ID.ACO.MCQM.YYYYQN.DATE.TIME.zip

    Uses DuckDB's read_xlsx() for legacy workbooks and read_csv() for 2026+
    CSVs, with UNION ALL BY NAME to handle schema drift across quarters.

    All column names are already uppercase in the source xlsx/csv files.

    FILE_DATE is the last calendar day of the quarter period encoded in the
    filename (e.g., 2025Q4 → 2025-12-31). A PERIOD column (e.g., '2025Q4')
    is added as a fifth metadata column alongside the standard FILE_PATH /
    DIRECTORY_NAME / FILE_NAME / FILE_DATE.
    """

    def _get_file_definitions(self) -> List[MCQMFileDef]:
        return MCQM_FILE_DEFS

    def _get_table_name(self, file_def: MCQMFileDef) -> str:
        return file_def.table_name

    def _find_zip_paths(self) -> List[str]:
        """List MCQM zip paths via DuckDB glob — works for local, S3, and ADLS
        because the session has already loaded and configured the relevant
        DuckDB extensions (httpfs/aws for S3, azure for ADLS).

        Legacy files are extension-less zips one level inside year/bundle directories.
        Starting in PY2026, files are .zip archives inside QEXPU bundle directories:
            FILE_STORE/ACO_ID/YEAR/P.ACO_ID*/P.ACO_ID.ACO.MCQM.YYYYQN.DATE.TIME
        """
        pattern = (
            f"{self.config.FILE_STORE}/{self.config.ACO_ID}/"
            f"*/*/P.{self.config.ACO_ID}*/P.{self.config.ACO_ID}.ACO.MCQM*"
        )
        try:
            rows = self.session.connection.execute(
                f"SELECT * FROM glob({sql_string_literal(pattern)})"
            ).fetchall()
        except duckdb.IOException as e:
            if is_empty_glob(e):
                return []
            raise SourceDiscoveryError(
                f"Could not list MCQM zip paths (pattern={pattern}): {e}"
            ) from e
        return sorted(r[0] for r in rows)

    def _period(self, filename: str) -> str:
        m = re.search(r"\.(\d{4}Q\d)\.", filename)
        return m.group(1) if m else ""

    def _period_year(self, filename: str) -> Optional[int]:
        m = re.search(r"\.(\d{4})Q\d\.", filename)
        return int(m.group(1)) if m else None

    def _quarter_end_date(self, filename: str):
        """Return the last calendar day of the quarter encoded in the filename.

        Matches the .YYYYQ#. segment (e.g., .2025Q4.) and returns a date.
        Returns None if the pattern is absent.
        """
        m = re.search(r"\.(\d{4})Q(\d)\.", filename)
        if not m:
            return None
        year, quarter = int(m.group(1)), int(m.group(2))
        end_month = quarter * 3
        if end_month == 12:
            return date(year, 12, 31)
        return date(year, end_month + 1, 1) - timedelta(days=1)

    def _list_source_file_paths(self, file_def: MCQMFileDef) -> List[Tuple[str, str]]:
        """Enumerate (file_path, source_path) for every MCQM source file.

        Uses DuckDB glob('zip://path!*.xlsx') / glob('zip://path!*.csv') to list
        zip contents. This works for local, S3, and ADLS because zipfs already has
        the session credentials configured, avoiding Python zipfile reads that
        can't open cloud URIs.

        source_path: 'zip://zip_path!internal_name' — the path passed to DuckDB
        file_path:   source_path with 'zip://' stripped — matches the FILE_PATH
                     value produced in _build_query()
        """
        zip_paths = self._find_zip_paths()
        result = []
        for zip_path in zip_paths:
            period_year = self._period_year(zip_path)
            is_csv_delivery = period_year is not None and period_year >= 2026
            if is_csv_delivery:
                glob_pattern = f"zip://{zip_path}!*.csv"
            elif file_def.sheet_name:
                glob_pattern = f"zip://{zip_path}!*.xlsx"
            else:
                continue

            try:
                rows = self.session.connection.execute(
                    f"SELECT * FROM glob({sql_string_literal(glob_pattern)})"
                ).fetchall()
            except duckdb.IOException as e:
                # zipfs raises "No files found that match the pattern" for an
                # archive that holds no matching member; that is a real empty.
                # Any other IOException (403, expired credentials, unreachable
                # store) means the archive could not be listed and must not be
                # read as "no measures delivered", which would skip the table
                # and still report success.
                if is_empty_glob(e):
                    continue
                raise SourceDiscoveryError(
                    f"Could not list MCQM zip contents (pattern={glob_pattern}): {e}"
                ) from e
            for row in rows:
                zip_ref = row[0]  # e.g. 'zip://s3://bucket/path/file!internal.csv'
                internal_name = zip_ref.split("!")[-1]
                if is_csv_delivery and not internal_name.endswith(file_def.csv_suffix):
                    continue
                if "Dictionary" in internal_name:
                    continue
                file_path = zip_ref.replace("zip://", "")
                result.append((file_path, zip_ref))
        return result

    def _build_query(self, file_def: MCQMFileDef, source_paths: List[str]) -> str:
        """Build a UNION ALL BY NAME query over the provided zipfs source paths."""
        selects = []
        for source_path in source_paths:
            # source_path is 'zip://zip_path!internal_name'
            zip_ref = source_path
            file_path, zip_path, internal_name, period, file_date_sql = (
                self._source_metadata(zip_ref)
            )

            if internal_name.lower().endswith(".csv"):
                selects.append(f"""
                    SELECT *,
                           {sql_string_literal(file_path)}     AS FILE_PATH,
                           {sql_string_literal(zip_path)}      AS DIRECTORY_NAME,
                           {sql_string_literal(internal_name)} AS FILE_NAME,
                           {file_date_sql} AS FILE_DATE,
                           {sql_string_literal(period)}        AS PERIOD
                    FROM read_csv({sql_string_literal(zip_ref)},
                                  header=true,
                                  auto_detect=true,
                                  auto_type_candidates=['VARCHAR'],
                                  union_by_name=true,
                                  ignore_errors=true)
                """)
                continue

            selects.append(f"""
                    SELECT *,
                           {sql_string_literal(file_path)}     AS FILE_PATH,
                           {sql_string_literal(zip_path)}      AS DIRECTORY_NAME,
                           {sql_string_literal(internal_name)} AS FILE_NAME,
                           {file_date_sql} AS FILE_DATE,
                           {sql_string_literal(period)}        AS PERIOD
                    FROM read_xlsx({sql_string_literal(zip_ref)},
                                   sheet={sql_string_literal(file_def.sheet_name or '')},
                                   header=true,
                                   all_varchar=true)
                """)

        print(f"  reading {len(source_paths)} source file(s)")
        return " UNION ALL BY NAME ".join(selects)

    def _source_metadata(self, zip_ref: str) -> Tuple[str, str, str, str, str]:
        """Return metadata values for a zipfs source reference."""
        file_path = zip_ref.replace("zip://", "")
        without_scheme = zip_ref[len("zip://"):]
        zip_path, _, internal_name = without_scheme.partition("!")
        combined = f"{zip_path}/{internal_name}"
        period = self._period(combined)
        file_date_val = self._quarter_end_date(combined)
        file_date_sql = f"DATE '{file_date_val}'" if file_date_val else "NULL::DATE"
        return file_path, zip_path, internal_name, period, file_date_sql
