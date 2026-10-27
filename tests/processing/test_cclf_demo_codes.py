"""CCLFA / CCLFB (benefit enhancement and demonstration codes) against IP v43.

medicare_cclf_connector v1.0.0 reads these files as
``source('medicare_cclf', 'parta_demo_codes')`` and ``'partb_demo_codes'``
(TUVA-125).
"""

from mssp_pipeline.processing.defs.cclf_file_defs import CCLF_FILE_DEFS

DEFS = {d.filename_pattern: d for d in CCLF_FILE_DEFS}


def test_demo_code_files_use_connector_table_names():
    assert DEFS["ZCA"].table_name == "parta_demo_codes"
    assert DEFS["ZCB"].table_name == "partb_demo_codes"


def test_cclfa_record_is_324_characters():
    assert len(DEFS["ZCA"].columns) == 33
    assert sum(c.width for c in DEFS["ZCA"].columns) == 324
