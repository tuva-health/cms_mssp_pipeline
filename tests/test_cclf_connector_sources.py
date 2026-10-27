"""The e2e's medicare_cclf_connector source-column check (TUVA-125).

The synthetic e2e (`tests/e2e/synthetic_run.py`) runs this comparison against
the tables `mssp-process` actually wrote; these tests pin the comparison and
the vendored sources file in the required `test` job.
"""

import pytest

from tests.e2e import cclf_sources
from tests.e2e.cclf_sources import connector_source_columns, source_column_violations

DECLARED = {
    "parta_demo_codes": ["cur_clm_uniq_id", "file_name"],
    "enrollment": ["bene_mbi_id"],
}


def test_vendored_sources_are_the_connector_tag():
    declared = connector_source_columns()
    assert "parta_demo_codes" in declared and "partb_demo_codes" in declared
    assert declared["partb_demo_codes"][-3:] == [
        "clm_line_carr_hpsa_scrcty_cd",
        "file_name",
        "file_date",
    ]


def test_an_edited_sources_file_is_rejected(tmp_path):
    edited = tmp_path / "sources.yml"
    edited.write_bytes(cclf_sources.SOURCES_PATH.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="re-copy it from the tag"):
        connector_source_columns(edited)


def test_matching_tables_pass_and_extra_metadata_columns_are_allowed():
    written = {
        "parta_demo_codes": ["cur_clm_uniq_id", "file_path", "file_name"],
        "cclf0_summary_stats_header_record": ["file_number_label"],
    }
    assert source_column_violations(written, DECLARED) == []


def test_missing_source_table_column_and_unknown_table_are_violations():
    assert source_column_violations({}, DECLARED) == [
        "parta_demo_codes: medicare_cclf_connector v1.0.0 source not written"
    ]
    assert source_column_violations({"parta_demo_codes": ["cur_clm_uniq_id"]}, DECLARED) == [
        "parta_demo_codes: missing medicare_cclf_connector v1.0.0 source columns ['file_name']"
    ]
    renamed = {
        "parta_demo_codes": ["cur_clm_uniq_id", "file_name"],
        "claims_benefit_enhancement_and_demonstration_code_file_cclfa": ["cur_clm_uniq_id"],
    }
    assert source_column_violations(renamed, DECLARED) == [
        "claims_benefit_enhancement_and_demonstration_code_file_cclfa: "
        "written, but not a medicare_cclf_connector v1.0.0 source"
    ]


def test_column_names_compare_exactly():
    written = {"parta_demo_codes": ["CUR_CLM_UNIQ_ID", "FILE_NAME"]}
    assert len(source_column_violations(written, DECLARED)) == 1
