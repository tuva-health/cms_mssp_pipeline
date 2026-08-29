from datetime import date
from typing import Dict, List

from .aco_workbook import ACOWorkbookProcessor, _date_literal, _varchar
from ..defs.aexpu_file_defs import AEXPU_SHEET_DEFS
from ..defs.sectioned_sheet_defs import SheetDef


class AEXPUProcessor(ACOWorkbookProcessor):
    """Processes the annual EXPU (Expenditure & Utilization) workbooks.

    One AEXPU workbook is delivered per benchmark year, inside the BNMRK bundle
    directory for the performance year::

        {FILE_STORE}/{ACO}/2025/{CODE}/P.{ACO}.ACO.BNMRK.D......T....../
            P.{ACO}.ACO.AEXPU.Y2022.D......T........xlsx   <- BY1
            P.{ACO}.ACO.AEXPU.Y2023.D......T........xlsx   <- BY2
            P.{ACO}.ACO.AEXPU.Y2024.D......T........xlsx   <- BY3

    So the glob has to key on the '.AEXPU.' filename token, not the bundle
    directory name. That token is also what keeps quarterly '.QEXPU.' files out.

    BENCHMARK_YEAR is the Y<year> token, PERIOD is 'Y<year>', and FILE_DATE is
    31 December of that benchmark year — the last day the workbook describes.
    """

    SHEET_DEFS: List[SheetDef] = AEXPU_SHEET_DEFS
    FILENAME_TOKEN = ".AEXPU."

    def _file_metadata_sql(self, xlsx_path: str) -> Dict[str, str]:
        metadata = super()._file_metadata_sql(xlsx_path)
        file_name = xlsx_path.replace("\\", "/").rsplit("/", 1)[-1]

        benchmark_year = self._benchmark_year(file_name)
        metadata["BENCHMARK_YEAR"] = _varchar(benchmark_year)
        metadata["PERIOD"] = _varchar(f"Y{benchmark_year}" if benchmark_year else None)
        metadata["FILE_DATE"] = _date_literal(
            date(int(benchmark_year), 12, 31) if benchmark_year else None
        )
        return metadata
