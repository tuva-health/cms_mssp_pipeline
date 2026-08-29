from typing import Dict, List

from .aco_workbook import ACOWorkbookProcessor, _date_literal, _varchar
from ..defs.qexpu_file_defs import QEXPU_SHEET_DEFS
from ..defs.sectioned_sheet_defs import SheetDef


class QEXPUProcessor(ACOWorkbookProcessor):
    """Processes the quarterly EXPU (Expenditure & Utilization) workbooks.

    One workbook per quarter, in its own quarterly bundle directory::

        {FILE_STORE}/{ACO}/2026/{CODE}/P.{ACO}.ACO.QEXPU.D......T....../
            P.{ACO}.ACO.QEXPU.2026Q1.D......T........xlsx

    The glob keys on the '.QEXPU.' filename token so annual '.AEXPU.' workbooks
    — which live in BNMRK bundles and would match a looser '*EXPU*' — are not
    swept up.

    PERIOD is the literal quarter ('2026Q1') and FILE_DATE the last day of that
    quarter, matching the convention MCQM and the retired EXPUProcessor used.
    """

    SHEET_DEFS: List[SheetDef] = QEXPU_SHEET_DEFS
    FILENAME_TOKEN = ".QEXPU."

    def _file_metadata_sql(self, xlsx_path: str) -> Dict[str, str]:
        metadata = super()._file_metadata_sql(xlsx_path)
        file_name = xlsx_path.replace("\\", "/").rsplit("/", 1)[-1]

        metadata["PERIOD"] = _varchar(self._quarter_label(file_name))
        metadata["FILE_DATE"] = _date_literal(self._quarter_end_date(file_name))
        return metadata
