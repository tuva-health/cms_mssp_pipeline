"""Processing subpackage: ETL pipeline for MSSP ACO files via DuckDB."""

import shutil
from pathlib import Path


def _cleanup_temp_location(config) -> None:
    """Delete all files written to TEMP_LOCATION during the run.

    Cloud exporters (Snowflake, Databricks, BigQuery, Redshift, Fabric) stage
    intermediate Parquet files under TEMP_LOCATION before uploading them.  Once
    the upload is done those files have no further use, so we remove them here
    to keep the working directory tidy.
    """
    temp = getattr(config, "TEMP_LOCATION", None)
    if not temp:
        return
    temp_path = Path(temp)
    if not temp_path.exists():
        return
    removed = 0
    for entry in temp_path.iterdir():
        if entry.is_file():
            entry.unlink()
            removed += 1
        elif entry.is_dir():
            shutil.rmtree(entry)
            removed += 1
    if removed:
        print(f"[cleanup] Removed {removed} staging file(s) from {temp_path}")


def run(config=None) -> None:
    """Run the full processing pipeline.

    Args:
        config: A config object with OUTPUT_TYPE, ACO_ID, FILE_STORE, FULL_REFRESH,
                and backend-specific fields. If None, loads from
                mssp_pipeline.processing.config (the user-editable config file).
    """
    if config is None:
        from mssp_pipeline.processing import config as _cfg
        config = _cfg

    from mssp_pipeline.processing.config_defs import validate_config
    from mssp_pipeline.processing.session import DuckDBSession
    from mssp_pipeline.processing.exporters import build_exporter
    from mssp_pipeline.processing.processors.cclf_processor import CCLFProcessor
    from mssp_pipeline.processing.processors.mssp_processor import MSSPProcessor
    from mssp_pipeline.processing.processors.mcqm_processor import MCQMProcessor
    from mssp_pipeline.processing.processors.shadow_bundles_processor import ShadowBundlesProcessor
    from mssp_pipeline.processing.processors.participant_list_processor import ParticipantListProcessor
    from mssp_pipeline.processing.processors.bnex_processor import BNEXProcessor
    from mssp_pipeline.processing.processors.bnex_mbi_xref_processor import BNEXMBIXrefProcessor
    from mssp_pipeline.processing.processors.expu_processor import EXPUProcessor

    from mssp_pipeline.processing.exceptions import ProcessingStartupError

    try:
        validate_config(config)
        session = DuckDBSession(config)
        exporter = build_exporter(config)
    except Exception as e:
        raise ProcessingStartupError(f"Startup error: {e}") from e

    try:
        CCLFProcessor(session, exporter, config).run()
        MSSPProcessor(session, exporter, config).run()
        MCQMProcessor(session, exporter, config).run()
        ShadowBundlesProcessor(session, exporter, config).run()
        ParticipantListProcessor(session, exporter, config).run()
        BNEXProcessor(session, exporter, config).run()
        BNEXMBIXrefProcessor(session, exporter, config).run()
        EXPUProcessor(session, exporter, config).run()
    finally:
        session.close()
        _cleanup_temp_location(config)
