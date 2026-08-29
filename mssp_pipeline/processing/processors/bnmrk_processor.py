from typing import Dict, List

from .aco_workbook import ACOWorkbookProcessor, _date_literal, _varchar
from ..defs.bnmrk_file_defs import BNMRK_SHEET_DEFS
from ..defs.sectioned_sheet_defs import SheetDef


class BNMRKProcessor(ACOWorkbookProcessor):
    """Processes the BNMRK (Historical Benchmark) workbook.

    BNMRK is a *bundle*, delivered up to three times per performance year — a
    March preliminary HB, a June preliminary/final HB and an October final HB —
    each into its own bundle directory alongside the annual AEXPU workbooks::

        {FILE_STORE}/{ACO}/{YEAR}/{CODE}/P.{ACO}.ACO.BNMRK.D......T......./
            P.{ACO}.ACO.BNMRK.D......T........xlsx      <- this processor
            P.{ACO}.ACO.AEXPU.Y2022.D......T........xlsx <- AEXPUProcessor

    The three deliveries have identical filenames apart from the D/T submission
    stamp, so SUBMISSION_ID is what tells them apart and PERIOD carries it too.

    'Table 6 - ACPT' exists only in the June and October deliveries; a missing
    sheet is a non-event handled by SectionedSheetProcessor.
    """

    SHEET_DEFS: List[SheetDef] = BNMRK_SHEET_DEFS
    FILENAME_TOKEN = ".BNMRK."

    def _file_metadata_sql(self, xlsx_path: str) -> Dict[str, str]:
        metadata = super()._file_metadata_sql(xlsx_path)
        file_name = xlsx_path.replace("\\", "/").rsplit("/", 1)[-1]

        # No year token in the filename at all — BENCHMARK_YEAR is not a BNMRK
        # concept and PERFORMANCE_YEAR comes from the directory layout.
        metadata["PERIOD"] = _varchar(self._submission_id(file_name))
        metadata["FILE_DATE"] = _date_literal(self._submission_date(file_name))
        return metadata
