import re
from datetime import date, timedelta
from typing import List, Tuple

from .base import FileProcessor
from ..defs.mcqm_file_defs import MCQMFileDef, MCQM_FILE_DEFS


class MCQMProcessor(FileProcessor):
    """
    Processes MCQM (Medicare Clinical Quality Measures) files.

    Files are xlsx workbooks (one worksheet per measure) embedded in
    extension-less zip archives inside year/bundle directories:
        FILE_STORE/ACO_ID/YEAR/P.ACO_ID*/P.ACO_ID.ACO.MCQM.YYYYQN.DATE.TIME

    Uses DuckDB's read_xlsx() (excel extension) with UNION ALL BY NAME to read
    all quarterly xlsx files in a single SQL query — no Python-level xlsx
    parsing, no TEMP TABLE, no row-by-row inserts. Schema drift across quarters
    (different column counts) is handled automatically by UNION ALL BY NAME.

    All column names are already uppercase in the source xlsx files.

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

        Files are extension-less zips one level inside year/bundle directories:
            FILE_STORE/ACO_ID/YEAR/P.ACO_ID*/P.ACO_ID.ACO.MCQM.YYYYQN.DATE.TIME
        """
        pattern = (
            f"{self.config.FILE_STORE}/{self.config.ACO_ID}/"
            f"*/*/P.{self.config.ACO_ID}*/P.{self.config.ACO_ID}.ACO.MCQM*"
        )
        try:
            rows = self.session.connection.execute(
                f"SELECT * FROM glob('{pattern}')"
            ).fetchall()
        except Exception:
            return []
        return sorted(r[0] for r in rows)

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
        """Enumerate (file_path, source_path) for every xlsx in every MCQM zip.

        Uses DuckDB glob('zip://path!*.xlsx') to list zip contents — this works for
        local, S3, and ADLS because zipfs already has the session credentials configured,
        avoiding the need for Python's zipfile.ZipFile() which can't open cloud URIs.

        source_path: 'zip://zip_path!xlsx_name' — the path passed to read_xlsx()
        file_path:   source_path with 'zip://' stripped — matches the FILE_PATH
                     value produced in _build_query()
        """
        zip_paths = self._find_zip_paths()
        result = []
        for zip_path in zip_paths:
            try:
                rows = self.session.connection.execute(
                    f"SELECT * FROM glob('zip://{zip_path}!*.xlsx')"
                ).fetchall()
            except Exception:
                continue
            for row in rows:
                zip_ref = row[0]  # e.g. 'zip://s3://bucket/path/file!internal.xlsx'
                xlsx_name = zip_ref.split("!")[-1]
                if "Dictionary" in xlsx_name:
                    continue
                file_path = zip_ref.replace("zip://", "")
                result.append((file_path, zip_ref))
        return result

    def _build_query(self, file_def: MCQMFileDef, source_paths: List[str]) -> str:
        """Build a UNION ALL BY NAME query over the provided zipfs source paths."""
        selects = []
        for source_path in source_paths:
            # source_path is 'zip://zip_path!xlsx_name'
            zip_ref = source_path
            file_path = zip_ref.replace("zip://", "")

            # Derive zip_path and xlsx_name for metadata
            without_scheme = zip_ref[len("zip://"):]
            zip_path, _, xlsx_name = without_scheme.partition("!")

            combined = f"{zip_path}/{xlsx_name}"
            period_m = re.search(r"\.(\d{4}Q\d)\.", combined)
            period = period_m.group(1) if period_m else ""
            file_date_val = self._quarter_end_date(combined)
            file_date_sql = (
                f"DATE '{file_date_val}'" if file_date_val else "NULL::DATE"
            )

            selects.append(f"""
                    SELECT *,
                           '{file_path}'   AS FILE_PATH,
                           '{zip_path}'    AS DIRECTORY_NAME,
                           '{xlsx_name}'   AS FILE_NAME,
                           {file_date_sql} AS FILE_DATE,
                           '{period}'      AS PERIOD
                    FROM read_xlsx('{zip_ref}',
                                   sheet='{file_def.sheet_name}',
                                   header=true,
                                   all_varchar=true)
                """)

        print(f"  reading {len(source_paths)} source file(s)")
        return " UNION ALL BY NAME ".join(selects)
