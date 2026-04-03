from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True)
class ColumnDef:
    name: str
    width: int


@dataclass(frozen=True)
class BNEXMBIXrefFileDef:
    table_name: str
    filename_pattern: str
    columns: List[ColumnDef]
    has_header: bool = False


BNEX_MBI_XREF_FILE_DEFS: List[BNEXMBIXrefFileDef] = [
    BNEXMBIXrefFileDef(
        table_name="excluded_beneficiary_mbi_xref",
        filename_pattern="MBI",
        has_header=False,
        columns=[
            ColumnDef("PERFORMANCE_YEAR", 4),
            ColumnDef("REPORT_MONTH", 2),
            ColumnDef("CURRENT_BENE_MBI", 11),
            ColumnDef("PREVIOUS_BENE_MBI", 11),
            ColumnDef("PREVIOUS_IDENTIFIER_EFFECTIVE_DATE", 8),
            ColumnDef("PREVIOUS_IDENTIFIER_OBSOLETE_DATE", 8),
        ],
    ),
    
]
