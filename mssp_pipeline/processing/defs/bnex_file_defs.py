from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class BNEXFileDef:
    table_name: str
    file_pattern: str


BNEX_FILE_DEFS: List[BNEXFileDef] = [
    BNEXFileDef("beneficiary_exclusions", "BNEX*.xml"),
]
