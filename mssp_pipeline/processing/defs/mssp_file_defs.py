from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class MSSPFileDef:
    table_name: str
    file_type: str         # e.g. 'AALR' — the report family this file belongs to
    filename_pattern: str  # glob pattern matched inside the zip, e.g. 'ALR*-1.csv'


MSSP_FILE_DEFS: List[MSSPFileDef] = [
    # AALR — Assignment List Reports
    MSSPFileDef("AALR1_ASSIGNED_BENEFICIARIES",               "AALR", "ALR*-1.csv"),
    MSSPFileDef("AALR2_ASSIGNED_BENEFICIARIES_TIN",           "AALR", "ALR*-2.csv"),
    MSSPFileDef("AALR4_ASSIGNED_BENEFICIARIES_TIN_NPI",       "AALR", "ALR*-4.csv"),
    MSSPFileDef("AALR5_BENEFICIARY_TURNOVER",                 "AALR", "ALR*-5.csv"),
    MSSPFileDef("AALR6_BENEFICIARIES_ASSIGNABLE_OR_VOLUNTARY","AALR", "ALR*-6.csv"),
    MSSPFileDef("AALR9_BENEFICIARIES_UNDERSERVED",            "AALR", "ALR*-9.csv"),
    # BAIP — Beneficiary Advance Investment Payments
    MSSPFileDef("BAIP_BENEFICIARY_ADVANCED_INVESTMENT_PAYMENT", "BAIP", "BAIP*.csv"),
    # BEUR — Beneficiary Expenditure Utilization Reports
    MSSPFileDef("BEUR_BENEFICIARY_EXPENDITURE_UTILIZATION_REPORT", "BEUR", "BEUR*.csv"),
    # NCBP — Non-Claims Based Payments
    MSSPFileDef("NCBP_NON_CLAIMS_BASED_PAYMENTS", "NCBP", "NCBP*.csv"),
]
