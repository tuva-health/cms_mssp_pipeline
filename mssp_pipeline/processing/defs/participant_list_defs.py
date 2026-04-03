from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class ParticipantListFileDef:
    table_name: str
    file_type: str         # e.g. 'AALR' — the report family this file belongs to
    filename_pattern: str  # glob pattern matched inside the zip, e.g. 'ALR*-1.csv'


PARTICIPANT_LIST_FILE_DEFS: List[ParticipantListFileDef] = [
    # Participant List files are in a single zip, organized by report family
    ParticipantListFileDef("PARTICIPANTS_LIST", "PARTICIPANTS_LIST", "Participants List*.csv"),
    ParticipantListFileDef("PROVIDER_AND_SUPPLIER_LIST", "PARTICIPANTS_LIST", "Providers and Suppliers List*.csv")
]
