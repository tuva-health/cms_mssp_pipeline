"""CCLFA / CCLFB (benefit enhancement and demonstration codes) against IP v43.

medicare_cclf_connector v1.0.0 reads these files as
``source('medicare_cclf', 'parta_demo_codes')`` and ``'partb_demo_codes'``
(TUVA-125). The CCLFB layout below is the CCLF Information Packet v43 record
layout: 21 fields, 128 characters. It is written out here on purpose, rather
than derived from the pipeline's definitions, so the test pins the spec.
"""

from mssp_pipeline.processing.defs.cclf_file_defs import CCLF_FILE_DEFS
from mssp_pipeline.processing.exporters.duckdb_exporter import DuckDBExporter
from mssp_pipeline.processing.processors.cclf_processor import CCLFProcessor
from tests.processing.conftest import ACO_ID, TEST_DATE_STR

DEFS = {d.filename_pattern: d for d in CCLF_FILE_DEFS}

# IP v43, CCLFB (Part B benefit enhancement and demonstration codes).
CCLFB_V43 = [
    ("CUR_CLM_UNIQ_ID", 13),
    ("CLM_LINE_NUM", 10),
    ("BENE_MBI_ID", 11),
    ("BENE_HIC_NUM", 11),
    ("CLM_TYPE_CD", 2),
    ("CLM_LINE_NGACO_PBPMT_SW", 1),
    ("CLM_LINE_NGACO_PDSCHRG_HCBS_SW", 1),
    ("CLM_LINE_NGACO_SNF_WVR_SW", 1),
    ("CLM_LINE_NGACO_TLHLTH_SW", 1),
    ("CLM_LINE_NGACO_CPTATN_SW", 1),
    ("CLM_DEMO_1ST_NUM", 2),
    ("CLM_DEMO_2ND_NUM", 2),
    ("CLM_DEMO_3RD_NUM", 2),
    ("CLM_DEMO_4TH_NUM", 2),
    ("CLM_DEMO_5TH_NUM", 2),
    ("CLM_PBP_INCLSN_AMT", 15),
    ("CLM_PBP_RDCTN_AMT", 15),
    ("CLM_NGACO_CMG_WVR_SW", 1),
    ("CLM_MDCR_DDCTBL_AMT", 19),
    ("CLM_SQSTRTN_RDCTN_AMT", 15),
    ("CLM_LINE_CARR_HPSA_SCRCTY_CD", 1),
]


def test_demo_code_files_use_connector_table_names():
    assert DEFS["ZCA"].table_name == "parta_demo_codes"
    assert DEFS["ZCB"].table_name == "partb_demo_codes"


def test_cclfa_record_is_324_characters():
    assert len(DEFS["ZCA"].columns) == 33
    assert sum(c.width for c in DEFS["ZCA"].columns) == 324


def test_cclfb_layout_matches_ip_v43():
    assert [(c.name, c.width) for c in DEFS["ZCB"].columns] == CCLFB_V43
    assert sum(c.width for c in DEFS["ZCB"].columns) == 128


def test_cclfb_record_parses_through_the_last_field(test_session, test_config, raw_dir):
    """A 128-character CCLFB record lands every trailing field in its column."""
    values = {
        "CUR_CLM_UNIQ_ID": "0000000000001",
        "CLM_LINE_NUM": "0000000001",
        "BENE_MBI_ID": "FAKEMBI0001",
        "CLM_TYPE_CD": "71",
        "CLM_PBP_INCLSN_AMT": "00000000012.34",
        "CLM_PBP_RDCTN_AMT": "00000000000.56",
        "CLM_NGACO_CMG_WVR_SW": "Y",
        "CLM_MDCR_DDCTBL_AMT": "0000000000000078.90",
        "CLM_SQSTRTN_RDCTN_AMT": "00000000001.23",
        "CLM_LINE_CARR_HPSA_SCRCTY_CD": "S",
    }
    record = "".join(str(values.get(name, "")).rjust(width) for name, width in CCLFB_V43)
    assert len(record) == 128

    bundle = raw_dir / ACO_ID / "2025" / "ZCY25" / f"P.{ACO_ID}.ACO.ZCY25"
    bundle.mkdir(parents=True)
    (bundle / f"P.{ACO_ID}.ACO.ZCBY25.D{TEST_DATE_STR}.T000000001").write_text(record)

    CCLFProcessor(test_session, DuckDBExporter(schema="raw_data", full_refresh=True), test_config).run()

    row = test_session.connection.execute(
        "SELECT clm_pbp_inclsn_amt, clm_pbp_rdctn_amt, clm_ngaco_cmg_wvr_sw, "
        "clm_mdcr_ddctbl_amt, clm_sqstrtn_rdctn_amt, clm_line_carr_hpsa_scrcty_cd "
        "FROM raw_data.partb_demo_codes"
    ).fetchall()
    assert row == [
        (
            "00000000012.34",
            "00000000000.56",
            "Y",
            "0000000000000078.90",
            "00000000001.23",
            "S",
        )
    ]
