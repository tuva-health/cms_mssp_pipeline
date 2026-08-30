from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class MCQMFileDef:
    table_name: str        # output table name, e.g. 'MCQM_BENEFICIARIES'
    sheet_name: str | None  # pre-2026 exact xlsx sheet name
    csv_suffix: str        # 2026+ internal CSV suffix, e.g. '_001.csv'


MCQM_FILE_DEFS: List[MCQMFileDef] = [
    MCQMFileDef(
        "MCQM_BENEFICIARIES", "Medicare_CQM_Beneficiaries", "_MCQMbenes.csv"
    ),
    MCQMFileDef("MCQM_DM_001SSP", "DM_001SSP", "_001.csv"),
    MCQMFileDef("MCQM_BCS_112SSP", "BCS_112SSP", "_112.csv"),
    MCQMFileDef("MCQM_CCS_113SSP", None, "_113.csv"),
    MCQMFileDef("MCQM_DEP_134SSP", "DEP_134SSP", "_134.csv"),
    MCQMFileDef("MCQM_HTN_236SSP", "HTN_236SSP", "_236.csv"),
]
