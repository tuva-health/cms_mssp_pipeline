from dataclasses import dataclass, fields

from mssp_pipeline.integration.remote_store import (
    contains_duckdb_glob_operator,
    is_remote_store,
    parse_remote_store,
    validate_source_identifier,
)


@dataclass(frozen=True)
class SnowflakeConfig:
    username: str
    account: str
    database: str
    schema: str
    warehouse: str
    role: str
    rsa_key_path: str
    rsa_key_passphrase: str = ""  # set to skip keyring prompt; leave empty to use keyring


@dataclass(frozen=True)
class DatabricksConfig:
    server_hostname: str   # e.g. "adb-1234567890.azuredatabricks.net"
    http_path: str         # SQL warehouse path e.g. "/sql/1.0/warehouses/abc123"
    access_token: str      # Personal Access Token
    schema: str            # Target schema / database name
    staging_path: str      # s3://..., abfss://..., or dbfs:/tmp/...
    catalog: str = ""      # Unity Catalog name — empty = legacy Hive metastore (schema.table)


@dataclass(frozen=True)
class RedshiftConfig:
    host: str              # Cluster endpoint or Serverless workgroup URL
    database: str          # Database name
    schema: str            # Target schema (e.g. 'raw_data')
    user: str              # Database username
    password: str          # Database password
    iam_role: str          # IAM role ARN the cluster uses to read from S3 (for COPY)
    staging_bucket: str    # S3 URI prefix: 's3://my-bucket/mssp-staging'
    port: int = 5439       # Default Redshift port


@dataclass(frozen=True)
class FabricConfig:
    onelake_path: str      # ABFSS Tables root: 'abfss://workspace@onelake.dfs.fabric.microsoft.com/lakehouse.Lakehouse/Tables'
    tenant_id: str         # Azure AD tenant ID
    client_id: str         # Service Principal client ID; empty = use env/managed identity
    client_secret: str     # Service Principal secret; empty = use env/managed identity


@dataclass(frozen=True)
class BigQueryConfig:
    project_id: str           # GCP project ID
    dataset_id: str           # BigQuery dataset name (e.g. 'raw_data')
    staging_bucket: str       # GCS URI prefix: 'gs://my-bucket/mssp-staging'
    credentials_path: str = ""  # Path to service account JSON; empty = Application Default Credentials
    location: str = "US"      # BigQuery dataset/job location


def validate_config(cfg) -> None:
    """Validate that required config fields are set for the selected OUTPUT_TYPE and FILE_STORE.

    Raises ValueError with a descriptive message on the first missing required value.
    """
    output_type = cfg.OUTPUT_TYPE

    def _require(var_name: str, value: str, context: str) -> str:
        if not value or not str(value).strip():
            raise ValueError(f"{context} requires {var_name} to be set in .env, the environment, or config.py")
        return value

    def _require_dataclass_fields(obj, required_fields: list[str], context: str) -> None:
        for field_name in required_fields:
            value = getattr(obj, field_name, "")
            if not value:
                raise ValueError(f"{context} requires {field_name} to be set in .env, the environment, or config.py")

    # --- Source identity and location validation (fail-closed) ---
    # ACO_ID and FILE_STORE are spliced verbatim into filesystem paths and
    # DuckDB glob patterns, so both must be present and safe before any listing
    # runs. Only generic, safe, non-empty composition is enforced here — no
    # client-specific identifier format.
    aco_id = _require("MSSP_ACO_ID", cfg.ACO_ID, "processing")
    try:
        validate_source_identifier(aco_id, field_name="MSSP_ACO_ID")
    except ValueError as exc:
        raise ValueError(f"Invalid MSSP_ACO_ID: {exc}") from exc

    file_store = _require("MSSP_FILE_STORE", cfg.FILE_STORE, "processing")
    source_scheme = None
    if is_remote_store(file_store):
        try:
            source_scheme = parse_remote_store(file_store).scheme
        except ValueError as exc:
            raise ValueError(f"Invalid MSSP_FILE_STORE: {exc}") from exc
    elif contains_duckdb_glob_operator(file_store):
        raise ValueError("Invalid MSSP_FILE_STORE: must not contain DuckDB glob operators")
    elif "://" in file_store:
        raise ValueError(f"Invalid MSSP_FILE_STORE: unsupported URI scheme in {file_store}")

    # --- Output backend validation ---
    if output_type in ("PARQUET", "DUCKDB"):
        _require("OUTPUT_LOCATION", cfg.OUTPUT_LOCATION, f"OUTPUT_TYPE='{output_type}'")

    elif output_type == "MOTHERDUCK":
        _require("MOTHERDUCK_DATABASE", cfg.MOTHERDUCK_DATABASE, "OUTPUT_TYPE='MOTHERDUCK'")

    elif output_type == "SNOWFLAKE":
        _require_dataclass_fields(
            cfg.SNOWFLAKE,
            ["username", "account", "database", "schema", "warehouse", "role", "rsa_key_path"],
            "OUTPUT_TYPE='SNOWFLAKE'",
        )

    elif output_type == "DATABRICKS":
        _require_dataclass_fields(
            cfg.DATABRICKS,
            ["server_hostname", "http_path", "access_token", "schema", "staging_path"],
            "OUTPUT_TYPE='DATABRICKS'",
        )

    elif output_type == "BIGQUERY":
        _require_dataclass_fields(
            cfg.BIGQUERY,
            ["project_id", "dataset_id", "staging_bucket"],
            "OUTPUT_TYPE='BIGQUERY'",
        )

    elif output_type == "REDSHIFT":
        _require_dataclass_fields(
            cfg.REDSHIFT,
            ["host", "database", "schema", "user", "password", "iam_role", "staging_bucket"],
            "OUTPUT_TYPE='REDSHIFT'",
        )

    elif output_type == "FABRIC":
        _require_dataclass_fields(
            cfg.FABRIC,
            ["onelake_path", "tenant_id"],
            "OUTPUT_TYPE='FABRIC'",
        )

    # --- Source backend validation ---
    if source_scheme == "s3":
        _require("AWS_REGION", cfg.AWS_REGION, "S3 FILE_STORE")

    elif source_scheme == "gs":
        _require("GCS_KEY_ID", cfg.GCS_KEY_ID, "GCS FILE_STORE")
        _require("GCS_SECRET", cfg.GCS_SECRET, "GCS FILE_STORE")

    elif source_scheme in {"az", "azure", "abfss"}:
        if not cfg.AZURE_STORAGE_CONNECTION_STRING and not cfg.AZURE_STORAGE_ACCOUNT:
            raise ValueError(
                "Azure FILE_STORE requires either AZURE_STORAGE_CONNECTION_STRING or "
                "AZURE_STORAGE_ACCOUNT to be set in .env, the environment, or config.py"
            )
