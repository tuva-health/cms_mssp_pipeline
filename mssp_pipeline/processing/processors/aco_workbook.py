import re
from datetime import date, timedelta
from typing import Dict, List, Optional, Set, Tuple

import duckdb

from .sectioned_sheet import SectionedSheetProcessor
from ..exceptions import SourceDiscoveryError, is_empty_glob
from ..sql import sql_string_literal

# The delivery filename always ends '.D<YYMMDD>.T<7 digits>.xlsx'. That pair is
# the only thing distinguishing the March / June / October BNMRK deliveries,
# which are otherwise identically named.
_SUBMISSION_ID_RE = re.compile(r"\.(D\d{6}\.T\d{7})\.xlsx$")


# Quarterly files carry the quarter: '...QEXPU.2026Q1.D...'.
_QUARTER_RE = re.compile(r"\.(\d{4})Q(\d)\.")

# The date stamp inside the submission id, as YYMMDD. CMS ships placeholder
# stamps such as 'D259999' in sample deliveries, which must not parse.
_SUBMISSION_DATE_RE = re.compile(r"\.D(\d{6})\.T\d{7}\.xlsx$")

_YEAR_RE = re.compile(r"\d{4}")

# A final settlement (STLMT) bundle directory: 'P.{ACO}.ACO.STLMT.D<YYMMDD>.T<7>'.
# Its files can carry the same D/T stamp as a benchmark delivery, so a settlement
# file's SUBMISSION_ID is the bundle's stamp behind this prefix.
SETTLEMENT_SUBMISSION_PREFIX = "STLMT."
_SETTLEMENT_BUNDLE_RE = re.compile(r"\.ACO\.STLMT\.(D\d{6}\.T\d{7})$")
_SETTLEMENT_BUNDLE_TOKEN = ".ACO.STLMT."

# CMS can deliver a settlement twice; the T stamp says which copy this is.
_EMBARGO_BY_T_STAMP = {"T0000000": True, "T1111111": False}


class ACOWorkbookProcessor(SectionedSheetProcessor):
    """Shared path handling for the BNMRK / AEXPU / QEXPU sectioned workbooks.

    All three live in the same organised file store::

        {FILE_STORE}/{ACO_ID}/{YEAR}/{CODE}/{BUNDLE_DIR}/{file}.xlsx

    and are told apart by a token in the *filename*, not the bundle directory
    name — the annual EXPU workbooks are delivered inside the BNMRK bundle, so
    matching on the directory would miss them. Subclasses set FILENAME_TOKEN
    (e.g. '.AEXPU.') and SHEET_DEFS, and may extend _file_metadata_sql().

    Every metadata value is derived from the path alone. That is a hard
    requirement, not a convenience: _list_source_file_paths() runs before any
    workbook is opened, so incremental dedup can decide what is new without
    reading file content.
    """

    # Overridden by subclasses, e.g. ".BNMRK." — matched against the filename.
    FILENAME_TOKEN: str = ""

    def __init__(self, session, exporter, config):
        super().__init__(session, exporter, config)
        # Notice-file embargo states per settlement bundle directory, so one
        # listing serves every sheet of every workbook in the bundle.
        self._notice_states_by_dir: Dict[str, Set[bool]] = {}

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def _glob_pattern(self) -> str:
        """{FILE_STORE}/{ACO}/{YEAR}/{CODE}/{BUNDLE_DIR}/P.{ACO}*<TOKEN>*.xlsx

        Three wildcard segments plus the filename — exactly the depth
        EXPUProcessor globs against the real S3 store
        ('{ACO}/*/*/P.{ACO}*EXPU*/*EXPU*.xlsx' is also year / code / bundle /
        file). The only change is that the discriminating token is matched on
        the filename rather than on the bundle directory name.
        """
        return (
            f"{self.config.FILE_STORE}/{self.config.ACO_ID}/*/*/*/"
            f"P.{self.config.ACO_ID}*{self.FILENAME_TOKEN}*.xlsx"
        )

    def _find_xlsx_paths(self) -> List[str]:
        """Discover workbook paths via DuckDB glob — local, S3 and ADLS alike."""
        pattern = self._glob_pattern()
        try:
            rows = self.session.connection.execute(
                f"SELECT * FROM glob({sql_string_literal(pattern)})"
            ).fetchall()
        except duckdb.IOException as e:
            # A glob that ran and matched nothing (local no-match, or the zipfs/
            # remote "No files found that match the pattern" IOException) means the
            # prefix is empty. Any other IOException — 403, wrong bucket, expired
            # credentials — means the listing itself failed and must not be read as
            # "no workbooks delivered yet", which would skip the table and still
            # report success.
            if is_empty_glob(e):
                return []
            raise SourceDiscoveryError(
                f"Could not list {self.__class__.__name__} source files "
                f"(pattern={pattern}): {e}"
            ) from e
        return sorted(r[0] for r in rows)

    def _list_source_file_paths(self, file_def) -> List[Tuple[str, str]]:
        """(FILE_PATH, source_path) for every matching workbook.

        FILE_PATH is the slash-normalised path — byte-for-byte what
        _file_metadata_sql() emits, so incremental dedup actually matches.

        A file whose '.D<YYMMDD>.T<7 digits>.xlsx' stamp does not parse is
        skipped with a warning rather than loaded. The glob is deliberately
        loose enough to catch strays ('....T1111111.backup.xlsx' matches it, and
        no reasonable tightening excludes it), and such a file would arrive with
        SUBMISSION_ID, PERIOD and FILE_DATE all NULL. For BNMRK the submission
        id is the *only* thing separating the March / June / October deliveries,
        so two unstamped files would merge into one indistinguishable identity.
        Loudly ignoring a stray beats silently corrupting delivery identity.
        """
        result: List[Tuple[str, str]] = []
        for path in self._find_xlsx_paths():
            path_norm = path.replace("\\", "/")
            if self._submission_id(path_norm.rsplit("/", 1)[-1]) is None:
                print(
                    f"  Warning: skipping {path_norm} — its filename carries no "
                    f"'.D<YYMMDD>.T<NNNNNNN>.xlsx' submission stamp, so the "
                    f"delivery it belongs to cannot be identified."
                )
                continue
            result.append((path_norm, path))
        return result

    # ------------------------------------------------------------------
    # Path-derived metadata
    # ------------------------------------------------------------------

    def _performance_year(self, path_norm: str) -> Optional[str]:
        """The path segment right after the ACO_ID segment, if it is 4 digits.

        BNMRK filenames carry no year token at all, so the directory layout is
        the only source.

        The scan runs from the end of the path backwards and returns the first
        4-digit successor it finds, rather than stopping at the last ACO-id
        segment. That ordering resolves the case where FILE_STORE itself ends in
        the ACO id ('.../A0000/A0000/2025/...' → '2025'), and continuing past a
        non-year successor resolves its mirror image ('.../A0000/2025/A0000/...'
        → '2025' as well). NULL only when no occurrence is followed by a year.
        """
        parts = path_norm.split("/")
        aco_id = str(self.config.ACO_ID)
        for index in range(len(parts) - 2, -1, -1):
            if parts[index] == aco_id and _YEAR_RE.fullmatch(parts[index + 1]):
                return parts[index + 1]
        return None

    def _submission_id(self, file_name: str) -> Optional[str]:
        """The file's own 'D<YYMMDD>.T<NNNNNNN>' stamp."""
        match = _SUBMISSION_ID_RE.search(file_name)
        return match.group(1) if match else None

    # ------------------------------------------------------------------
    # Settlement identity
    # ------------------------------------------------------------------

    @staticmethod
    def _bundle_dir_name(path_norm: str) -> str:
        parts = path_norm.split("/")
        return parts[-2] if len(parts) >= 2 else ""

    def _is_settlement(self, path_norm: str) -> bool:
        """True for a file inside a STLMT bundle directory.

        Keyed on the bundle directory, not the filename: the settlement AEXPU is
        named exactly like a benchmark AEXPU, and only its directory says which
        delivery it came with.
        """
        return _SETTLEMENT_BUNDLE_TOKEN in self._bundle_dir_name(path_norm)

    def _delivery_submission_id(self, path_norm: str) -> Optional[str]:
        """SUBMISSION_ID: which delivery a workbook belongs to.

        Benchmark and quarterly files keep the bare file stamp. A settlement file
        gets 'STLMT.' plus its bundle directory's stamp (the file's own stamp when
        the directory carries none). The prefix keeps a settlement off a benchmark
        delivery that shares its stamp; the bundle stamp keeps every file of one
        settlement delivery together and the embargoed and unembargoed copies
        apart.
        """
        file_stamp = self._submission_id(path_norm.rsplit("/", 1)[-1])
        if not self._is_settlement(path_norm):
            return file_stamp
        match = _SETTLEMENT_BUNDLE_RE.search(self._bundle_dir_name(path_norm))
        stamp = match.group(1) if match else file_stamp
        return SETTLEMENT_SUBMISSION_PREFIX + stamp if stamp else None

    def _is_embargoed(self, path_norm: str) -> Optional[bool]:
        """IS_EMBARGOED for a settlement file; None for anything else.

        The delivery's T stamp decides first (T0000000 embargoed, T1111111
        unembargoed). Any other stamp falls back to the Embargoed/Unembargoed
        FinRec notice file in the bundle directory. No evidence, or notices
        saying both, gives None rather than a guess.
        """
        if not self._is_settlement(path_norm):
            return None
        submission_id = self._delivery_submission_id(path_norm) or ""
        t_stamp = submission_id.rsplit(".", 1)[-1]
        if t_stamp in _EMBARGO_BY_T_STAMP:
            return _EMBARGO_BY_T_STAMP[t_stamp]
        states = self._notice_embargo_states(path_norm.rsplit("/", 1)[0])
        return states.pop() if len(states) == 1 else None

    def _notice_embargo_states(self, directory: str) -> Set[bool]:
        """The embargo states the bundle's FinRec notice files declare."""
        cache = self._notice_states_by_dir
        if directory not in cache:
            states: Set[bool] = set()
            for name in self._directory_file_names(directory):
                letters = re.sub(r"[^a-z]", "", name.lower())
                if "unembargo" in letters:
                    states.add(False)
                elif "embargo" in letters:
                    states.add(True)
            cache[directory] = states
        return set(cache[directory])

    def _directory_file_names(self, directory: str) -> List[str]:
        pattern = f"{directory}/*"
        try:
            rows = self.session.connection.execute(
                f"SELECT * FROM glob({sql_string_literal(pattern)})"
            ).fetchall()
        except duckdb.IOException as e:
            if is_empty_glob(e):
                return []
            raise SourceDiscoveryError(
                f"Could not list settlement bundle {directory}: {e}"
            ) from e
        return [r[0].replace("\\", "/").rsplit("/", 1)[-1] for r in rows]

    def _benchmark_year(self, file_name: str) -> Optional[str]:
        """The Y<year> token that immediately follows the family token.

        Anchored to FILENAME_TOKEN rather than searched loosely, because a bare
        '\\.Y(\\d{4})\\.' takes the *leftmost* match: an ACO id shaped like
        'Y2024' would make 'P.Y2024.ACO.AEXPU.Y2022.D...' report benchmark year
        2024 — and BENCHMARK_YEAR is the only thing separating the BY1/BY2/BY3
        workbooks of one bundle.
        """
        match = re.search(
            rf"{re.escape(self.FILENAME_TOKEN)}Y(\d{{4}})\.", file_name
        )
        return match.group(1) if match else None

    def _submission_date(self, file_name: str) -> Optional[date]:
        """The D<YYMMDD> stamp as a date, or None when it is a placeholder.

        Sample deliveries use stamps such as 'D259999' that are not dates. NULL
        is the honest answer there — the same try_strptime-and-accept-NULL
        contract CCLFProcessor uses for its filename dates.
        """
        match = _SUBMISSION_DATE_RE.search(file_name)
        if not match:
            return None
        stamp = match.group(1)
        try:
            return date(2000 + int(stamp[0:2]), int(stamp[2:4]), int(stamp[4:6]))
        except ValueError:
            return None

    def _quarter_label(self, file_name: str) -> Optional[str]:
        """The literal quarter token in the filename, e.g. '2026Q1'."""
        match = _QUARTER_RE.search(file_name)
        return f"{match.group(1)}Q{match.group(2)}" if match else None

    def _quarter_end_date(self, file_name: str) -> Optional[date]:
        """Last calendar day of the quarter encoded in the filename (2026Q1 →
        2026-03-31). Copied from the retired EXPUProcessor."""
        match = _QUARTER_RE.search(file_name)
        if not match:
            return None
        year, quarter = int(match.group(1)), int(match.group(2))
        end_month = quarter * 3
        if end_month == 12:
            return date(year, 12, 31)
        return date(year, end_month + 1, 1) - timedelta(days=1)

    def _file_metadata_sql(self, xlsx_path: str) -> Dict[str, str]:
        """FILE_PATH/DIRECTORY_NAME/FILE_NAME/FILE_DATE plus the shared ACO columns.

        Subclasses fill in BENCHMARK_YEAR, PERIOD and FILE_DATE, which are the
        three that differ between annual, quarterly and benchmark deliveries.
        """
        metadata = super()._file_metadata_sql(xlsx_path)
        path_norm = xlsx_path.replace("\\", "/")
        file_name = path_norm.rsplit("/", 1)[-1]

        metadata["ACO_ID"] = _varchar(str(self.config.ACO_ID))
        metadata["PERFORMANCE_YEAR"] = _varchar(self._performance_year(path_norm))
        metadata["BENCHMARK_YEAR"] = _varchar(None)
        metadata["SUBMISSION_ID"] = _varchar(self._delivery_submission_id(path_norm))
        metadata["PERIOD"] = _varchar(None)
        metadata["IS_EMBARGOED"] = _boolean(self._is_embargoed(path_norm))
        return metadata


def _varchar(value: Optional[str]) -> str:
    """A VARCHAR-typed literal, so a NULL column still unions with a populated one."""
    return "CAST(NULL AS VARCHAR)" if value is None else sql_string_literal(value)


def _boolean(value: Optional[bool]) -> str:
    """A BOOLEAN-typed literal, so a NULL flag still unions with a set one."""
    return "CAST(NULL AS BOOLEAN)" if value is None else ("TRUE" if value else "FALSE")


def _date_literal(value: Optional[date]) -> str:
    return "NULL::DATE" if value is None else f"DATE '{value}'"
