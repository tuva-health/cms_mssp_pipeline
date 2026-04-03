from .base import Exporter


def build_exporter(config) -> Exporter:
    """
    Factory: reads OUTPUT_TYPE and FULL_REFRESH from config and returns the
    appropriate Exporter. This is the single place in the codebase where
    either flag is consulted. Backends are imported lazily so that missing
    optional dependencies only raise errors for the backend actually in use.
    """
    output_type = config.OUTPUT_TYPE
    full_refresh = getattr(config, 'FULL_REFRESH', False)

    if output_type == 'PARQUET':
        from .parquet_exporter import ParquetExporter
        return ParquetExporter(output_dir=config.OUTPUT_LOCATION, full_refresh=full_refresh)
    elif output_type in ('DUCKDB', 'MOTHERDUCK'):
        from .duckdb_exporter import DuckDBExporter
        return DuckDBExporter(schema='raw_data', full_refresh=full_refresh)
    elif output_type == 'SNOWFLAKE':
        from .snowflake_exporter import SnowflakeExporter
        return SnowflakeExporter(sf_config=config.SNOWFLAKE, staging_dir=config.TEMP_LOCATION, full_refresh=full_refresh)
    elif output_type == 'DATABRICKS':
        from .databricks_exporter import DatabricksExporter
        return DatabricksExporter(db_config=config.DATABRICKS, staging_dir=config.TEMP_LOCATION, full_refresh=full_refresh)
    elif output_type == 'BIGQUERY':
        from .bigquery_exporter import BigQueryExporter
        return BigQueryExporter(bq_config=config.BIGQUERY, staging_dir=config.TEMP_LOCATION, full_refresh=full_refresh)
    elif output_type == 'REDSHIFT':
        from .redshift_exporter import RedshiftExporter
        return RedshiftExporter(rs_config=config.REDSHIFT, staging_dir=config.TEMP_LOCATION, full_refresh=full_refresh)
    elif output_type == 'FABRIC':
        from .fabric_exporter import FabricExporter
        return FabricExporter(fabric_config=config.FABRIC, staging_dir=config.TEMP_LOCATION, full_refresh=full_refresh)
    else:
        raise ValueError(f"Unknown OUTPUT_TYPE: {output_type!r}. Must be PARQUET, DUCKDB, MOTHERDUCK, SNOWFLAKE, DATABRICKS, BIGQUERY, REDSHIFT, or FABRIC.")
