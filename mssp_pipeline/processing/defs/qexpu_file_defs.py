from typing import List

from .sectioned_sheet_defs import SheetDef

# As with AEXPU, the underscores in 'Table_1-Aggregate_EU_Report' are escaped so
# LIKE treats them as literals rather than single-character wildcards.
# Table 2 (regional expenditures) is quarterly-only; there is no Table 4.
QEXPU_SHEET_DEFS: List[SheetDef] = [
    SheetDef(table_name="QEXPU_TABLE_1", sheet_pattern=r"Table\_1-%"),
    SheetDef(table_name="QEXPU_TABLE_2", sheet_pattern=r"Table\_2-%"),
    SheetDef(table_name="QEXPU_TABLE_3", sheet_pattern=r"Table\_3-%"),
    SheetDef(
        table_name="QEXPU_PARAMETERS",
        sheet_pattern="Parameters",
        synthetic_column_labels=("VALUE",),
    ),
]
