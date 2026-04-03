from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class ShadowBundleFileDef:
    table_name: str
    file_type: str         # e.g. 'AALR' — the report family this file belongs to
    filename_pattern: str  # glob pattern matched inside the zip, e.g. 'ALR*-1.csv'


SHADOW_BUNDLES_FILE_DEFS: List[ShadowBundleFileDef] = [
    # Shadow bundles contain multiple source files in a single zip, organized by report family
    ShadowBundleFileDef("SHADOW_BUNDLES_DM", "SHADOW_BUNDLES", "DM_*.csv"),
    ShadowBundleFileDef("SHADOW_BUNDLES_EPI", "SHADOW_BUNDLES", "EPI_*.csv"),
    ShadowBundleFileDef("SHADOW_BUNDLES_HH", "SHADOW_BUNDLES", "HH_*.csv"),
    ShadowBundleFileDef("SHADOW_BUNDLES_HS", "SHADOW_BUNDLES", "HS_*.csv"),
    ShadowBundleFileDef("SHADOW_BUNDLES_IP", "SHADOW_BUNDLES", "IP_*.csv"),
    ShadowBundleFileDef("SHADOW_BUNDLES_OPL", "SHADOW_BUNDLES", "OPL_*.csv"),
    ShadowBundleFileDef("SHADOW_BUNDLES_PB", "SHADOW_BUNDLES", "PB_*.csv"),
    ShadowBundleFileDef("SHADOW_BUNDLES_SN", "SHADOW_BUNDLES", "SN_*.csv"),
]
