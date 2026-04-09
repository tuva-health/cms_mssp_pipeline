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
            ColumnDef("performance_year", 4),
            ColumnDef("report_month", 2),
            ColumnDef("current_bene_mbi", 11),
            ColumnDef("previous_bene_mbi", 11),
            ColumnDef("previous_identifier_effective_date", 8),
            ColumnDef("previous_identifier_obsolete_date", 8),
        ],
    ),
    
]
