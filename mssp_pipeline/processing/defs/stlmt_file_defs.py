from typing import List

from .sectioned_sheet_defs import SheetDef

# The final settlement (financial reconciliation) workbook. Like BNMRK, its
# sheet names carry no underscores, and the ' - ' is what keeps 'Table 1 - %'
# off 'Table 1A - ...' and 'Table A1 - %' off 'Table A1x - ...'. Cover, TOC and
# Glossary carry no data and have no def.
#
# 'Table A6 - ACPT' is present only for agreements on the accountable care
# prospective trend, as BNMRK 'Table 6 - ACPT' is, so it is optional. Every
# other sheet is required.
#
# The layout options follow the real PY2025 workbook (TUVA-167):
# - Table 2: the [A] and [B] heading rows carry sub-header text
#   ('3-Year Average Benchmark', 'BY3'), not values.
# - Table 3: one value column, 'Performance Year', under a blank column A.
# - Table A1: its letters label the columns ('... [I]'); STLMTProcessor's
#   layout columns cut them into COLUMN_CODE, so it needs no option.
# - Table A2: two blocks, each a title, a header row and the type rows.
# - Table A4: a lookup grid whose column A is data.
STLMT_SHEET_DEFS: List[SheetDef] = [
    SheetDef(table_name="STLMT_TABLE_1", sheet_pattern="Table 1 - %"),
    SheetDef(table_name="STLMT_TABLE_1A", sheet_pattern="Table 1A - %"),
    SheetDef(table_name="STLMT_TABLE_1B", sheet_pattern="Table 1B - %"),
    SheetDef(table_name="STLMT_TABLE_1C", sheet_pattern="Table 1C - %"),
    SheetDef(
        table_name="STLMT_TABLE_2", sheet_pattern="Table 2 - %", heading_cells_are_labels=True
    ),
    SheetDef(table_name="STLMT_TABLE_3", sheet_pattern="Table 3 - %", min_header_cells=1),
    SheetDef(table_name="STLMT_TABLE_A1", sheet_pattern="Table A1 - %"),
    SheetDef(table_name="STLMT_TABLE_A2", sheet_pattern="Table A2 - %", blocks=True),
    SheetDef(table_name="STLMT_TABLE_A3", sheet_pattern="Table A3 - %"),
    SheetDef(
        table_name="STLMT_TABLE_A4", sheet_pattern="Table A4 - %", label_column_is_data=True
    ),
    SheetDef(table_name="STLMT_TABLE_A5", sheet_pattern="Table A5 - %"),
    SheetDef(table_name="STLMT_TABLE_A6", sheet_pattern="Table A6 - %", optional=True),
    SheetDef(
        table_name="STLMT_PARAMETERS",
        sheet_pattern="Parameters",
        synthetic_column_labels=("VALUE",),
    ),
]
