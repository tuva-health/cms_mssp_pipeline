from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class EXPUFileDef:
    table_name: str             # e.g. "EXPU_TABLE_1"
    sheet_prefix: str           # e.g. "Table_1" — matched with LIKE 'Table_1%'
    add_section_column: bool = False  # propagate section-header rows as SECTION column
    unpivot_periods: bool = False     # unpivot period columns to (PERIOD_COLUMN, VALUE) rows


EXPU_FILE_DEFS: List[EXPUFileDef] = [
    EXPUFileDef(table_name="EXPU_TABLE_1", sheet_prefix="Table_1", add_section_column=True),
    EXPUFileDef(table_name="EXPU_TABLE_2", sheet_prefix="Table_2", unpivot_periods=True),
    EXPUFileDef(table_name="EXPU_TABLE_3", sheet_prefix="Table_3", add_section_column=True),
]
