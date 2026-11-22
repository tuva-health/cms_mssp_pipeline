import re
from typing import Dict, List, Optional, Tuple

from .aco_workbook import ACOWorkbookProcessor, _date_literal, _file_name, _varchar
from ..defs.sectioned_sheet_defs import SheetDef
from ..defs.stlmt_file_defs import STLMT_SHEET_DEFS

_FILENAME_TOKEN = ".STLMT.Y"

# The financial reconciliation workbook is named
# 'P.{ACO}.ACO.STLMT.Y<PY>.D<YYMMDD>.T<NNNNNNN>.xlsx': the year token runs
# straight into the stamp. Anything else carrying '.STLMT.Y' is not it.
_WORKBOOK_NAME_RE = re.compile(
    rf"{re.escape(_FILENAME_TOKEN)}(\d{{4}})\.D\d{{6}}\.T\d{{7}}\.xlsx$"
)


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

    Its tables carry the layout grammar (BLOCK_LABEL, ROW_KIND, COLUMN_CODE)
    on top of the shared cell grain, so the real layout survives: Table A1's
    lettered columns, Table 2's total row, Table A2's two blocks.
    """

    SHEET_DEFS: List[SheetDef] = STLMT_SHEET_DEFS
    FILENAME_TOKEN = _FILENAME_TOKEN
    # BLOCK_LABEL, ROW_KIND and COLUMN_CODE (see SectionedSheetProcessor).
    LAYOUT_COLUMNS = True

    def _list_source_file_paths(self, file_def) -> List[Tuple[str, str]]:
        """The base listing, narrowed to files named exactly like the workbook.

        PERFORMANCE_YEAR comes from the directory layout, as it does for the
        settlement AEXPU beside the workbook. A 'Y<year>' token that disagrees
        with it is reported, not silently overridden.
        """
        result = []
        for path_norm, path in super()._list_source_file_paths(file_def):
            name_year = self._name_year(_file_name(path_norm))
            if name_year is None:
                print(
                    f"  Warning: skipping {path_norm} — it carries the "
                    f"'{self.FILENAME_TOKEN}' token but is not named "
                    f"'...STLMT.Y<year>.D<YYMMDD>.T<NNNNNNN>.xlsx'."
                )
                continue
            directory_year = self._performance_year(path_norm)
            if directory_year != name_year:
                print(
                    f"  Warning: {path_norm} — its filename says performance "
                    f"year {name_year} but it is filed under {directory_year}; "
                    f"PERFORMANCE_YEAR follows the directory."
                )
            result.append((path_norm, path))
        return result

    def _settlement_stamp(self, path_norm: str) -> Optional[str]:
        """Every file this processor reads is a settlement. One filed outside a
        STLMT bundle directory falls back to its own stamp, so it never takes a
        bare stamp a benchmark delivery could share."""
        stamp = super()._settlement_stamp(path_norm)
        return stamp if stamp is not None else self._file_stamp(_file_name(path_norm))

    def _name_year(self, file_name: str) -> Optional[str]:
        """The performance year the workbook's own 'Y<year>' token names."""
        match = _WORKBOOK_NAME_RE.search(file_name)
        return match.group(1) if match else None

    def _file_metadata_sql(self, xlsx_path: str) -> Dict[str, str]:
        metadata = super()._file_metadata_sql(xlsx_path)
        file_name = _file_name(xlsx_path.replace("\\", "/"))

        year = self._name_year(file_name)
        metadata["PERIOD"] = _varchar(f"Y{year}" if year else None)
        metadata["FILE_DATE"] = _date_literal(self._submission_date(file_name))
        return metadata
