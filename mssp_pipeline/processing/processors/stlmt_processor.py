import re
from typing import Dict, List, Optional, Tuple

from .aco_workbook import ACOWorkbookProcessor, _date_literal, _file_name, _varchar
from ..defs.sectioned_sheet_defs import SheetDef
from ..defs.stlmt_file_defs import STLMT_SHEET_DEFS

# The financial reconciliation workbook is named
# 'P.{ACO}.ACO.STLMT.Y<PY>.D<YYMMDD>.T<NNNNNNN>.xlsx': the year token runs
# straight into the stamp. Anything else carrying '.STLMT.Y' is not it.
_WORKBOOK_NAME_RE = re.compile(r"\.STLMT\.Y\d{4}\.D\d{6}\.T\d{7}\.xlsx$")


class STLMTProcessor(ACOWorkbookProcessor):
    """Processes the final settlement (financial reconciliation) workbook.

    CMS sends one settlement bundle per performance year, around August to
    November of PY+1, and can send it twice: embargoed (T0000000) and
    unembargoed (T1111111). Both copies carry the same workbook::

        {FILE_STORE}/{ACO}/{PY}/{CODE}/P.{ACO}.ACO.STLMT.D......T......./
            P.{ACO}.ACO.STLMT.Y{PY}.D......T........xlsx   <- this processor
            P.{ACO}.ACO.AEXPU.Y{PY}.D......T........xlsx   <- AEXPUProcessor

    Both copies load, as two submissions. SUBMISSION_ID ('STLMT.' plus the
    bundle stamp) and IS_EMBARGOED come from ACOWorkbookProcessor; a reader
    prefers IS_EMBARGOED = FALSE when both are present.

    PERIOD is the 'Y<PY>' filename token and FILE_DATE the D stamp (NULL for a
    placeholder such as D259999). BENCHMARK_YEAR is not a settlement concept.
    """

    SHEET_DEFS: List[SheetDef] = STLMT_SHEET_DEFS
    FILENAME_TOKEN = ".STLMT.Y"

    def _list_source_file_paths(self, file_def) -> List[Tuple[str, str]]:
        """The base listing, narrowed to files named exactly like the workbook."""
        result = []
        for path_norm, path in super()._list_source_file_paths(file_def):
            if not _WORKBOOK_NAME_RE.search(_file_name(path_norm)):
                print(
                    f"  Warning: skipping {path_norm}: it carries the "
                    f"'{self.FILENAME_TOKEN}' token but is not named "
                    f"'...STLMT.Y<year>.D<YYMMDD>.T<NNNNNNN>.xlsx'."
                )
                continue
            result.append((path_norm, path))
        return result

    def _settlement_stamp(self, path_norm: str) -> Optional[str]:
        """Every file this processor reads is a settlement. One filed outside a
        STLMT bundle directory falls back to its own stamp, so it never takes a
        bare stamp a benchmark delivery could share."""
        stamp = super()._settlement_stamp(path_norm)
        return stamp if stamp is not None else self._file_stamp(_file_name(path_norm))

    def _performance_year_token(self, file_name: str) -> Optional[str]:
        match = re.search(r"\.STLMT\.Y(\d{4})\.", file_name)
        return match.group(1) if match else None

    def _file_metadata_sql(self, xlsx_path: str) -> Dict[str, str]:
        metadata = super()._file_metadata_sql(xlsx_path)
        file_name = _file_name(xlsx_path.replace("\\", "/"))

        year = self._performance_year_token(file_name)
        metadata["PERIOD"] = _varchar(f"Y{year}" if year else None)
        metadata["FILE_DATE"] = _date_literal(self._submission_date(file_name))
        return metadata
