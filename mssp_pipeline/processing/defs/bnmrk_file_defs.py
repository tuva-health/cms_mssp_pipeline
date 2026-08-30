from typing import List

from .sectioned_sheet_defs import SheetDef

# The Historical Benchmark workbook's sheet names carry no underscores, so a
# plain LIKE pattern is safe; the ' - ' is what keeps 'Table 1 - %' from also
# matching 'Table 1A - Regional Adjustment'.
BNMRK_SHEET_DEFS: List[SheetDef] = [
    SheetDef(table_name="BNMRK_TABLE_1", sheet_pattern="Table 1 - %"),
    SheetDef(table_name="BNMRK_TABLE_1A", sheet_pattern="Table 1A - %"),
    SheetDef(table_name="BNMRK_TABLE_1B", sheet_pattern="Table 1B - %"),
    SheetDef(table_name="BNMRK_TABLE_1C", sheet_pattern="Table 1C - %"),
    SheetDef(table_name="BNMRK_TABLE_2", sheet_pattern="Table 2 - %"),
    SheetDef(table_name="BNMRK_TABLE_3", sheet_pattern="Table 3 - %"),
    SheetDef(table_name="BNMRK_TABLE_4", sheet_pattern="Table 4 - %"),
    SheetDef(table_name="BNMRK_TABLE_5", sheet_pattern="Table 5 - %"),
    # Present only in the June and October deliveries, not the March preliminary.
    SheetDef(table_name="BNMRK_TABLE_6", sheet_pattern="Table 6 - %"),
    SheetDef(
        table_name="BNMRK_PARAMETERS",
        sheet_pattern="Parameters",
        synthetic_column_labels=("VALUE",),
    ),
]
