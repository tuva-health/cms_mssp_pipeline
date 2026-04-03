from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class MCQMFileDef:
    table_name: str   # output table name, e.g. 'MCQM_BENEFICIARIES'
    sheet_name: str   # exact xlsx sheet name, e.g. 'Medicare_CQM_Beneficiaries'


MCQM_FILE_DEFS: List[MCQMFileDef] = [
    MCQMFileDef("MCQM_BENEFICIARIES", "Medicare_CQM_Beneficiaries"),
    MCQMFileDef("MCQM_DM_001SSP",     "DM_001SSP"),
    MCQMFileDef("MCQM_BCS_112SSP",    "BCS_112SSP"),
    MCQMFileDef("MCQM_DEP_134SSP",    "DEP_134SSP"),
    MCQMFileDef("MCQM_HTN_236SSP",    "HTN_236SSP"),
]
