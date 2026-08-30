from typing import List

from .sectioned_sheet_defs import SheetDef

# AEXPU sheet names use underscores ('Table_1-Aggregate_EU_Report'), which are
# LIKE's single-character wildcard — hence the '\_' escapes. The trailing '-'
# is what separates 'Table\_1-%' from 'Table_1A-EU_Excluding_COVID'.
#
# The COVID-excluding variants (1A and 4A) are present in the BY1 and BY2
# workbooks and absent from BY3 — verified across all six real AEXPU deliveries
# (Y2022/Y2023/Y2024 x two bundles). AEXPU_TABLE_1A and AEXPU_TABLE_4A are
# therefore legitimately empty for the most recent benchmark year; a missing row
# there is expected, not an error. The per-batch missing-sheet path in
# SectionedSheetProcessor covers the workbooks that lack them.
AEXPU_SHEET_DEFS: List[SheetDef] = [
    SheetDef(table_name="AEXPU_TABLE_1", sheet_pattern=r"Table\_1-%"),
    SheetDef(table_name="AEXPU_TABLE_1A", sheet_pattern=r"Table\_1A-%"),
    SheetDef(table_name="AEXPU_TABLE_3", sheet_pattern=r"Table\_3-%"),
    SheetDef(table_name="AEXPU_TABLE_4", sheet_pattern=r"Table\_4-%"),
    SheetDef(table_name="AEXPU_TABLE_4A", sheet_pattern=r"Table\_4A-%"),
    SheetDef(
        table_name="AEXPU_PARAMETERS",
        sheet_pattern="Parameters",
        synthetic_column_labels=("VALUE",),
    ),
]
